"""Tests for stream_ai_service_regenerate (ticket 09): the pi reimplementation of
the legacy regenerate endpoint — validate the target user message, drop every
later message in the session, apply optional overrides in place, then re-run the
turn through the same relay as a fresh send.

The DB is stubbed (no SQLite/MySQL) by patching the DB-touching helpers and
capturing the delete/override behavior.
"""
import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.exceptions import AppException
from app.services import ai_chat_service
from app.services.ai_chat_service import stream_ai_service_regenerate


def _drain_events(generator):
    async def _collect():
        return [event async for event in generator]

    return asyncio.run(_collect())


class _DbStub:
    def __init__(self, target_message) -> None:
        self._target = target_message
        self.executed = []
        self.added = []
        self.commits = 0

    def get(self, model, key):
        return self._target

    def execute(self, stmt):
        self.executed.append(stmt)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, *_args, **_kwargs):
        pass

    def scalar(self, *_args, **_kwargs):
        return None


def _stub_chat_config(model_ref: str | None = "deepseek/deepseek-v4-flash-vision-exp"):
    return SimpleNamespace(
        model_ref=model_ref,
        context_messages=12,
        chat_billing_override=lambda: {"points_per_call": 5.0, "points_per_million_input_tokens": None, "points_per_million_output_tokens": None},
    )


def _caps_patch(multimodal: bool | None = None):
    """Patch the catalog capability lookup (ticket 11): None → permissive empty map."""
    capabilities = {} if multimodal is None else {"deepseek/deepseek-v4-flash-vision-exp": {"multimodal": multimodal}}
    return patch.object(ai_chat_service, "get_catalog_capabilities", new=AsyncMock(return_value=capabilities))


def _fake_save(db, session, user, resume, reply, suggestions, optimized_resume_data, tool_trace=None, pending_questions=None):
    return SimpleNamespace(model_dump=lambda *_a, **_k: {"id": 99, "content": reply})


def _relay_patches(model_config=None, capabilities=None):
    return (
        patch.object(ai_chat_service, "resolve_chat_ai_config", return_value=model_config or _stub_chat_config()),
        patch.object(ai_chat_service, "precheck_flow_points"),
        _caps_patch() if capabilities is None else _caps_patch(capabilities),
        patch.object(ai_chat_service, "get_or_create_chat_session", return_value=SimpleNamespace(id=42, update_time=None)),
        patch.object(ai_chat_service, "build_ai_service_context", return_value={"resume": {}, "history": [], "pending_change": {}}),
        patch.object(ai_chat_service, "flag_modified"),
        patch.object(ai_chat_service, "_save_assistant_message", side_effect=_fake_save),
    )


class StreamAiServiceRegenerateTest(unittest.TestCase):
    def _target_message(self, **overrides):
        base = {
            "id": 10,
            "session_id": 42,
            "user_id": 7,
            "resume_id": 3,
            "role": "user",
            "content": "帮我把地点改成深圳",
            "attachments": [],
            "suggestions": [],
            "optimized_resume_data": None,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_regen_deletes_later_messages_and_reruns_relay(self) -> None:
        target = self._target_message()
        db = _DbStub(target)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, title="后端简历", resume_data={"basics": {}})

        def faux_events(*_args, **_kwargs):
            async def gen():
                yield {"type": "delta", "text": "重新生成的回复"}
                yield {"type": "result", "data": {}}

            return gen()

        patches = _relay_patches()
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with patch.object(ai_chat_service, "ai_service_chat_events", side_effect=faux_events) as events:
                results = _drain_events(stream_ai_service_regenerate(db, user, resume, 10))

        # start event carries the target user message id so the frontend can splice.
        start = [event for event in results if event["type"] == "start"][0]
        self.assertEqual(start["data"]["user_message_id"], 10)
        # later messages deleted (session-scoped), turn re-run through the relay.
        self.assertEqual(len(db.executed), 1)
        events.assert_called_once()
        relay_kwargs = events.call_args.kwargs
        self.assertEqual(relay_kwargs.get("user_id"), 7)
        self.assertEqual(relay_kwargs.get("resume_id"), 3)
        # result rewritten with the persisted assistant row.
        result_event = [event for event in results if event["type"] == "result"][0]
        self.assertEqual(result_event["data"]["assistant_message"]["id"], 99)

    def test_regen_rejects_foreign_or_assistant_message(self) -> None:
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, resume_data={})
        cases = [
            None,
            self._target_message(user_id=8),
            self._target_message(resume_id=4),
            self._target_message(role="assistant"),
        ]
        for target in cases:
            with self.subTest(target=role_or_none(target)):
                with patch.object(ai_chat_service, "resolve_chat_ai_config", return_value=_stub_chat_config()):
                    results = _drain_events(stream_ai_service_regenerate(_DbStub(target), user, resume, 10))
                self.assertEqual(results[0]["type"], "error")
                self.assertIn("自己的用户消息", results[0]["message"])

    def test_regen_applies_content_and_attachment_overrides(self) -> None:
        target = self._target_message()
        db = _DbStub(target)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, resume_data={})

        def faux_events(*_args, **_kwargs):
            async def gen():
                yield {"type": "delta", "text": "好的"}
                yield {"type": "result", "data": {}}

            return gen()

        attachments = [{"url": "http://x/1.png", "object_name": "ai-chat/1.png", "content_type": "image/png"}]
        patches = _relay_patches()
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with patch.object(ai_chat_service, "ai_service_chat_events", side_effect=faux_events) as events:
                _drain_events(
                    stream_ai_service_regenerate(db, user, resume, 10, override_content="  重新问：看下工作经历  ", override_attachments=attachments)
                )

        self.assertEqual(target.content, "重新问：看下工作经历")
        self.assertEqual(target.attachments, attachments)
        self.assertIn(target, db.added)
        # The overridden content, not the original, drives the model turn.
        self.assertEqual(events.call_args.args[0], "重新问：看下工作经历")

    def test_regen_rejects_empty_override_without_attachments(self) -> None:
        target = self._target_message()
        db = _DbStub(target)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, resume_data={})

        patches = _relay_patches()
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            results = _drain_events(
                stream_ai_service_regenerate(db, user, resume, 10, override_content="   ", override_attachments=[])
            )

        self.assertEqual(results[0]["type"], "error")
        self.assertIn("不能为空", results[0]["message"])
        self.assertEqual(db.executed, [], "nothing is deleted when validation fails")

    def test_regen_rejects_images_on_text_only_model(self) -> None:
        target = self._target_message()
        db = _DbStub(target)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, resume_data={})

        patches = _relay_patches(model_config=_stub_chat_config(), capabilities=False)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            results = _drain_events(
                stream_ai_service_regenerate(
                    db, user, resume, 10,
                    override_attachments=[{"url": "http://x/1.png", "content_type": "image/png", "object_name": "ai-chat/1.png"}],
                )
            )

        self.assertEqual(results[0]["type"], "error")
        self.assertIn("多模态", results[0]["message"])
        # Guard failures must abort BEFORE the delete: later history survives.
        self.assertEqual(db.executed, [], "no messages are deleted when the image guard rejects")

    def test_regen_still_runs_without_any_model_configured(self) -> None:
        target = self._target_message()
        db = _DbStub(target)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, resume_data={})

        def faux_events(*_args, **_kwargs):
            async def gen():
                yield {"type": "delta", "text": "faux 回复"}
                yield {"type": "result", "data": {}}

            return gen()

        patches = _relay_patches()
        with ExitStack() as stack:
            stack.enter_context(patch.object(ai_chat_service, "resolve_chat_ai_config", side_effect=AppException("未配置大模型 API KEY")))
            for p in patches[1:]:
                stack.enter_context(p)
            with patch.object(ai_chat_service, "ai_service_chat_events", side_effect=faux_events):
                results = _drain_events(stream_ai_service_regenerate(db, user, resume, 10))

        result_event = [event for event in results if event["type"] == "result"][0]
        self.assertEqual(result_event["data"]["assistant_message"]["content"], "faux 回复")


def role_or_none(message):
    return getattr(message, "role", None)


if __name__ == "__main__":
    unittest.main()
