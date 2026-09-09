import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services.ai_chat_service import _message_out, _stream_ai_service_reply


def _event_source(*events):
    async def _gen(*args, **kwargs):
        for event in events:
            yield event

    return _gen


class _FakeAssistant:
    def model_dump(self, mode: str = "json"):
        return {"id": 9, "role": "assistant", "pending_questions": "from-db"}


class PendingQuestionsRelayTest(unittest.TestCase):
    """The result event's pending_questions (ask_user_questions tool) must be
    sanitized and threaded into _save_assistant_message so the answer dialog
    survives page reloads."""

    def _run(self, result_questions):
        result_event = {
            "type": "result",
            "data": {
                "assistant_message": {"content": "已向你收集补充信息"},
                "pending_questions": result_questions,
            },
        }
        with (
            patch("app.services.ai_chat_service.build_ai_service_context", return_value={}),
            patch("app.services.ai_chat_service.ai_service_chat_events", return_value=_event_source(result_event)()),
            patch(
                "app.services.ai_chat_service._save_assistant_message",
                return_value=_FakeAssistant(),
            ) as mock_save,
        ):
            async def _collect():
                return [event async for event in _stream_ai_service_reply(
                    db=None,
                    user=SimpleNamespace(id=1),
                    resume=SimpleNamespace(id=2),
                    session=SimpleNamespace(id=3, context_summary=None, summary_message_id=None),
                    user_message=SimpleNamespace(content="/JD优化 后端 JD", attachments=[]),
                    model_config=None,
                )]

            events = asyncio.run(_collect())
        return events, mock_save

    def test_questions_are_persisted(self) -> None:
        questions = [{"question": "是否有百万级 DAU 的项目经历？", "hint": "例如：在 XX 公司负责 XX 产品"}]
        _, mock_save = self._run(questions)
        self.assertEqual(mock_save.call_args.kwargs["pending_questions"], questions)

    def test_non_list_questions_are_dropped(self) -> None:
        _, mock_save = self._run("junk")
        self.assertIsNone(mock_save.call_args.kwargs["pending_questions"])

    def test_question_items_must_be_dicts(self) -> None:
        _, mock_save = self._run(["not-a-dict", {"question": "是否有管理经验？"}])
        self.assertEqual(mock_save.call_args.kwargs["pending_questions"], [{"question": "是否有管理经验？"}])


class MessageOutPendingQuestionsTest(unittest.TestCase):
    def test_message_out_carries_pending_questions(self) -> None:
        questions = [{"question": "是否有管理经验？"}]
        message = SimpleNamespace(
            id=5,
            role="assistant",
            content="已向你收集补充信息",
            attachments=[],
            suggestions=[],
            optimized_resume_data=None,
            tool_trace=None,
            timing=None,
            pending_questions=questions,
            action_status="none",
            create_time=datetime.now(),
        )
        out = _message_out(message)
        self.assertEqual(out.pending_questions, questions)


if __name__ == "__main__":
    unittest.main()
