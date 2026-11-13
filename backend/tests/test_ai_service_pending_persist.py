"""Tests that stream_ai_service_chat persists the validated pending change when the
agent's result event carries a pending_change payload (from update_basics_field).

This is the DB-side integration point of ticket 03: the Node tool validates the
field-level change via the callback endpoint, the agent echoes the payload back on
the result event, and here we confirm FastAPI hands the merged resume data +
suggestions to _save_assistant_message (which sets action_status="pending").

The DB is stubbed (no SQLite/MySQL) by patching the DB-touching helpers and
capturing the call to _save_assistant_message.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.exceptions import AppException
from app.services import ai_chat_service
from app.services.ai_chat_service import stream_ai_service_chat


def _drain_events(generator):
    async def _collect():
        return [event async for event in generator]

    return asyncio.run(_collect())


class _DbStub:
    """Minimal session stand-in: the DB-touching helpers are patched, so only the
    user-message add/commit/refresh and the (patched) assistant save run here.

    The service opens its own session now (FastAPI closes the request-scoped one
    before the response body starts), so the stub is injected as a factory.
    """

    def __init__(self) -> None:
        self.added = []
        self.closed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, *_args, **_kwargs):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def scalar(self, *_args, **_kwargs):
        return None

_OPTIMIZED = {
    "basics": {"name": "张三", "title": "后端工程师", "location": "深圳", "phone": "13800138000"},
    "summary": {"content": "五年前端经验"},
}
_SUGGESTIONS = ["已修改：基本信息（所在城市）"]


def _result_event():
    return {
        "type": "result",
        "data": {"pending_change": {"optimized_resume_data": _OPTIMIZED, "suggestions": _SUGGESTIONS}},
    }


def _faux_events(*_args, **_kwargs):
    async def gen():
        yield {"type": "delta", "text": "好的"}
        yield _result_event()

    return gen()


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


class StreamAiServiceMultimodalTest(unittest.TestCase):
    """The pi-agent path only forwards uploaded images to a vision-capable model
    (ticket 06 acceptance criterion 3): a non-multimodal model must get a clear
    error, never a silently-dropped image."""

    def test_rejects_image_when_model_not_multimodal(self) -> None:
        config = _stub_chat_config()
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, title="后端简历", resume_data={"basics": {}})
        attachments = [{"url": "http://127.0.0.1:9000/vitaflow/ai-chat/1.png", "object_name": "ai-chat/1.png", "content_type": "image/png"}]

        with (
            patch.object(ai_chat_service, "resolve_chat_ai_config", return_value=config),
            patch.object(ai_chat_service, "precheck_flow_points"),
            _caps_patch(multimodal=False),
            patch.object(ai_chat_service, "owned_object_names", side_effect=lambda _db, _uid, names: set(names)),
            patch.object(ai_chat_service, "ai_service_chat_events", side_effect=_faux_events) as events,
        ):
            results = _drain_events(stream_ai_service_chat(user, resume, "按图片改简历", attachments, db_factory=_DbStub))

        self.assertEqual(results[0]["type"], "error")
        self.assertIn("多模态", results[0]["message"])
        events.assert_not_called()

    def test_forwards_attachments_when_model_is_multimodal(self) -> None:
        config = _stub_chat_config()
        session = SimpleNamespace(id=42, title="AI 简历助手", update_time=None, context_summary=None, summary_message_id=None)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, title="后端简历", resume_data={"basics": {"name": "张三"}})
        attachments = [{"url": "http://127.0.0.1:9000/vitaflow/ai-chat/1.png", "object_name": "ai-chat/1.png", "content_type": "image/png"}]

        captured: dict = {}

        def faux_events(*_args, **_kwargs):
            captured["attachments"] = _kwargs.get("attachments")
            captured["model_ref"] = _kwargs.get("model_ref")

            async def gen():
                yield {"type": "delta", "text": "我看到了图片"}
                yield {"type": "result", "data": {}}

            return gen()

        with (
            patch.object(ai_chat_service, "resolve_chat_ai_config", return_value=config),
            patch.object(ai_chat_service, "precheck_flow_points") as precheck,
            _caps_patch(),
            patch.object(ai_chat_service, "get_or_create_chat_session", return_value=session),
            patch.object(ai_chat_service, "build_ai_service_context", return_value={"resume": {}, "history": [], "pending_change": {}}),
            patch.object(ai_chat_service, "ai_service_chat_events", side_effect=faux_events),
            patch.object(ai_chat_service, "owned_object_names", side_effect=lambda _db, _uid, names: set(names)),
            patch.object(ai_chat_service, "_save_assistant_message", side_effect=lambda *a, **k: SimpleNamespace(timing=None, model_dump=lambda *_a, **_k: {"id": 1, "content": "我看到了图片"})),
        ):
            _drain_events(stream_ai_service_chat(user, resume, "按图片改简历", attachments, db_factory=_DbStub))

        # The proxy forwards the attachment so the Node side can resolve it into a
        # base64 ImageContent block for the model.
        self.assertEqual(captured["attachments"], attachments)
        # The mapped pi model ref rides along so the Node service selects the
        # right model from models.json (ticket 07).
        self.assertEqual(captured["model_ref"], "deepseek/deepseek-v4-flash-vision-exp")
        precheck.assert_called_once()


class StreamAiServicePendingPersistTest(unittest.TestCase):
    def test_persists_pending_payload_when_result_carries_pending_change(self):
        captured = {}

        def fake_save(db, session, user, resume, reply, suggestions, optimized_resume_data, tool_trace=None, pending_questions=None, timing=None, pending_info_fragments=None):
            captured["suggestions"] = suggestions
            captured["optimized_resume_data"] = optimized_resume_data
            captured["reply"] = reply
            return SimpleNamespace(timing=None, model_dump=lambda *_a, **_k: {"id": 1, "content": reply})

        session = SimpleNamespace(id=42, title="AI 简历助手", update_time=None, context_summary=None, summary_message_id=None)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, title="后端简历", resume_data={"basics": {"location": "广州"}})

        with (
            patch.object(ai_chat_service, "resolve_chat_ai_config", return_value=_stub_chat_config()),
            patch.object(ai_chat_service, "precheck_flow_points"),
            _caps_patch(),
            patch.object(ai_chat_service, "get_or_create_chat_session", return_value=session),
            patch.object(ai_chat_service, "build_ai_service_context", return_value={"resume": {}, "history": [], "pending_change": {}}),
            patch.object(ai_chat_service, "ai_service_chat_events", side_effect=_faux_events),
            patch.object(ai_chat_service, "_save_assistant_message", side_effect=fake_save) as save,
        ):
            results = _drain_events(stream_ai_service_chat(user, resume, "帮我把地点从广州改成深圳", db_factory=_DbStub))

        # Only one assistant persistence call, and it carries the validated pending data.
        self.assertEqual(save.call_count, 1)
        self.assertEqual(captured["optimized_resume_data"], _OPTIMIZED)
        self.assertEqual(captured["suggestions"], _SUGGESTIONS)
        self.assertEqual(captured["reply"], "好的")
        # The result event rewrites assistant_message to the DB row.
        result_event = [event for event in results if event["type"] == "result"][0]
        self.assertEqual(result_event["data"]["assistant_message"]["content"], "好的")

    def test_persists_plain_message_when_no_pending_change(self):
        captured = {}

        def faux_events(*_args, **_kwargs):
            async def gen():
                yield {"type": "delta", "text": "这是普通回复"}
                yield {"type": "result", "data": {}}

            return gen()

        def fake_save(db, session, user, resume, reply, suggestions, optimized_resume_data, tool_trace=None, pending_questions=None, timing=None, pending_info_fragments=None):
            captured["suggestions"] = suggestions
            captured["optimized_resume_data"] = optimized_resume_data
            return SimpleNamespace(timing=None, model_dump=lambda *_a, **_k: {"id": 1, "content": reply})

        session = SimpleNamespace(id=42, title="AI 简历助手", update_time=None, context_summary=None, summary_message_id=None)
        user = SimpleNamespace(id=7)
        resume = SimpleNamespace(id=3, title="后端简历", resume_data={"basics": {}})

        with (
            # No model configured at all (pure faux demo): the turn runs unmapped
            # and unbilled — the tolerant resolve path (ticket 07).
            patch.object(ai_chat_service, "resolve_chat_ai_config", side_effect=AppException("未配置大模型 API KEY")),
            _caps_patch(),
            patch.object(ai_chat_service, "get_or_create_chat_session", return_value=session),
            patch.object(ai_chat_service, "build_ai_service_context", return_value={"resume": {}, "history": [], "pending_change": {}}),
            patch.object(ai_chat_service, "ai_service_chat_events", side_effect=faux_events),
            patch.object(ai_chat_service, "_save_assistant_message", side_effect=fake_save) as save,
        ):
            _drain_events(stream_ai_service_chat(user, resume, "早上好", db_factory=_DbStub))

        self.assertEqual(save.call_count, 1)
        self.assertIsNone(captured["optimized_resume_data"])
        self.assertEqual(captured["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
