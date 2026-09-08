"""Tests for the rolling chat-context summary compaction (长对话记忆).

The compaction runs as a fire-and-forget background task after a chat turn:
messages beyond the injected window are LLM-summarized into
``ai_chat_sessions.context_summary`` (covering everything up to
``summary_message_id``) and re-injected on the next turn. All DB access goes
through a fake session stand-in; the one-shot LLM call and the task ledger
helpers are patched (local venv cannot load _sqlite3, same pattern as
test_ai_service_context).
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.ai_chat import AiChatMessage, AiChatSession
from app.services.ai_chat_service import (
    _compact_chat_context,
    _schedule_chat_summary,
    build_ai_service_context,
)


class _DbStub:
    """Scalar-only stand-in for build_ai_service_context / _schedule_chat_summary."""

    def __init__(self, pending=None, count=0):
        self._pending = pending
        self._count = count

    def scalar(self, *args, **kwargs):
        return self._pending if self._pending is not None else self._count


class _FakeCompactionDb:
    """Stand-in for the compaction's SessionLocal session."""

    def __init__(self, session_obj, rows):
        self._session = session_obj
        self._rows = rows
        self.commits = 0

    def get(self, _model, _pk):
        return self._session

    def scalars(self, *args, **kwargs):
        # 真实查询按 id desc 取数（最新 context_limit 条保留原文）。
        return list(reversed(self._rows))

    def add(self, _obj):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _session() -> AiChatSession:
    return AiChatSession(id=1, user_id=7, resume_id=3, title="t", context_summary=None, summary_message_id=None)


def _rows(count: int) -> list[AiChatMessage]:
    rows = []
    for i in range(1, count + 1):
        rows.append(AiChatMessage(id=i, session_id=1, user_id=7, resume_id=3, role="user" if i % 2 else "assistant", content=f"消息{i}"))
    return rows


class ContextSummaryInjectionTest(unittest.TestCase):
    def _resume_stub(self):
        return SimpleNamespace(id=3, title="后端简历", resume_data={"basics": {"title": "后端工程师"}})

    def test_context_injects_summary_and_filters_history_by_watermark(self):
        history = [SimpleNamespace(role="user", content="最新消息")]
        with patch(
            "app.services.ai_chat_service.list_chat_messages", return_value=history
        ) as mock_list:
            context = build_ai_service_context(
                _DbStub(pending=None),
                SimpleNamespace(id=7),
                self._resume_stub(),
                limit=12,
                summary="用户想投后端岗，薪资期望 25k",
                summary_message_id=99,
            )

        self.assertEqual(context["context_summary"], "用户想投后端岗，薪资期望 25k")
        # history must be fetched from beyond the summary watermark
        self.assertEqual(mock_list.call_args.kwargs.get("after_id"), 99)
        self.assertEqual(context["history"], [{"role": "user", "content": "最新消息"}])

    def test_context_summary_defaults_to_empty(self):
        with patch("app.services.ai_chat_service.list_chat_messages", return_value=[]):
            context = build_ai_service_context(_DbStub(pending=None), SimpleNamespace(id=7), self._resume_stub())
        self.assertEqual(context["context_summary"], "")


class ScheduleChatSummaryTest(unittest.TestCase):
    def test_triggers_background_task_over_threshold(self):
        with (
            patch("app.services.ai_chat_service.list_chat_messages", return_value=[]),
            patch("asyncio.create_task") as mock_create,
        ):
            # count 21 > limit 12 + 8 → compaction scheduled
            _schedule_chat_summary(_DbStub(count=21), _session(), 12, "deepseek/deepseek-v4")
        mock_create.assert_called_once()

    def test_skips_background_task_within_threshold(self):
        with (
            patch("app.services.ai_chat_service.list_chat_messages", return_value=[]),
            patch("asyncio.create_task") as mock_create,
        ):
            # count 20 == limit 12 + 8 → nothing scheduled
            _schedule_chat_summary(_DbStub(count=20), _session(), 12, "deepseek/deepseek-v4")
        mock_create.assert_not_called()


class CompactChatContextTest(unittest.TestCase):
    def _run_compact(self, fake_db, *, result=({"summary": "用户想投后端岗"}, [{"input_tokens": 10, "output_tokens": 5}]), raise_error=None):
        finished = {}

        async def fake_task_result(task_type, input_payload, *, model_ref=None, transport=None):
            if raise_error:
                raise raise_error
            return result

        with (
            patch("app.services.ai_chat_service.SessionLocal", return_value=fake_db),
            patch("app.services.ai_chat_service.create_ai_task", return_value=SimpleNamespace(id=55)) as mock_create_task,
            patch("app.services.ai_chat_service.ai_service_task_result", side_effect=fake_task_result),
            patch("app.services.ai_chat_service.finish_ai_task", side_effect=lambda db, t, status, **kw: finished.update(status=status, kwargs=kw, task=t)),
        ):
            asyncio.run(_compact_chat_context(1, 2, "deepseek/deepseek-v4"))
        return mock_create_task, finished

    def test_compacts_window_overflow_and_updates_session(self):
        session_obj = _session()
        fake_db = _FakeCompactionDb(session_obj, _rows(6))
        _create_task, finished = self._run_compact(fake_db)

        # newest context_limit=2 stay verbatim; ids 1..4 are compacted
        self.assertEqual(session_obj.summary_message_id, 4)
        self.assertEqual(session_obj.context_summary, "用户想投后端岗")
        self.assertEqual(fake_db.commits, 1)

        created_input = _create_task.call_args.args[5] if len(_create_task.call_args.args) > 5 else None
        self.assertEqual(_create_task.call_args.args[2], "chat_summary")
        self.assertEqual(finished["status"], "success")
        # 平台成本：摘要不计费，只记任务行
        self.assertFalse(finished["kwargs"].get("settle", True))
        self.assertEqual(finished["kwargs"]["token_usage"]["total_tokens"], 15)

    def test_no_compaction_when_history_within_window(self):
        session_obj = _session()
        fake_db = _FakeCompactionDb(session_obj, _rows(2))
        _create_task, finished = self._run_compact(fake_db)

        _create_task.assert_not_called()
        self.assertEqual({}, finished)
        self.assertIsNone(session_obj.summary_message_id)

    def test_failure_records_failed_task_and_keeps_session(self):
        session_obj = _session()
        fake_db = _FakeCompactionDb(session_obj, _rows(6))
        _create_task, finished = self._run_compact(fake_db, raise_error=RuntimeError("模型超时"))

        self.assertEqual(finished["status"], "failed")
        self.assertIn("模型超时", finished["kwargs"].get("error_message") or "")
        # 失败不推进 watermark，下轮重试同一段
        self.assertIsNone(session_obj.summary_message_id)
        self.assertIsNone(session_obj.context_summary)


if __name__ == "__main__":
    unittest.main()
