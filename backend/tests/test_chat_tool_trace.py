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
        return {"id": 9, "role": "assistant", "tool_trace": "from-db"}


class ToolTraceRelayTest(unittest.TestCase):
    """_stream_ai_service_reply must relay `tool` events verbatim and persist the
    result event's tool_trace (sanitized) alongside the assistant message."""

    def _relay(self, events):
        async def _collect():
            out = []
            async for event in _stream_ai_service_reply(
                db=None,
                user=SimpleNamespace(id=1),
                resume=SimpleNamespace(id=2),
                session=SimpleNamespace(id=3, context_summary=None, summary_message_id=None),
                user_message=SimpleNamespace(content="帮我看下工作经历", attachments=[]),
                model_config=None,
            ):
                out.append(event)
            return out

        return _collect

    def _run(self, result_tool_trace):
        tool_event = {"type": "tool", "data": {"id": "tc1", "name": "get_resume_context", "label": "查阅简历「实习/工作经历」", "status": "start"}}
        result_event = {
            "type": "result",
            "data": {
                "assistant_message": {"content": "好的"},
                "tool_trace": result_tool_trace,
            },
        }
        with (
            patch("app.services.ai_chat_service.build_ai_service_context", return_value={}),
            patch("app.services.ai_chat_service.ai_service_chat_events", return_value=_event_source(tool_event, result_event)()),
            patch(
                "app.services.ai_chat_service._save_assistant_message",
                return_value=_FakeAssistant(),
            ) as mock_save,
        ):
            events = asyncio.run(self._relay(None)())

        return events, mock_save

    def test_tool_event_relays_and_trace_is_persisted(self) -> None:
        trace = [{"id": "tc1", "name": "get_resume_context", "label": "查阅简历「实习/工作经历」", "status": "end", "ok": True}]
        events, mock_save = self._run(trace)

        # The streamed `tool` event passes through untouched for live rendering.
        self.assertIn({"type": "tool", "data": {"id": "tc1", "name": "get_resume_context", "label": "查阅简历「实习/工作经历」", "status": "start"}}, events)
        # The rewritten result carries the saved assistant row.
        result = next(event for event in events if event["type"] == "result")
        self.assertEqual(result["data"]["assistant_message"]["tool_trace"], "from-db")
        # The trace reached _save_assistant_message sanitized (list of dicts).
        self.assertEqual(mock_save.call_args.args[-1], trace)

    def test_non_list_trace_is_dropped(self) -> None:
        events, mock_save = self._run("junk")
        self.assertTrue(any(event["type"] == "result" for event in events))
        self.assertIsNone(mock_save.call_args.args[-1])

    def test_trace_items_must_be_dicts(self) -> None:
        events, mock_save = self._run(["not-a-dict", {"id": "tc2"}])
        self.assertTrue(any(event["type"] == "result" for event in events))
        self.assertEqual(mock_save.call_args.args[-1], [{"id": "tc2"}])


class MessageOutTraceTest(unittest.TestCase):
    """_message_out must surface tool_trace — the frontend replaces its live
    steps with the persisted row on completion, so a dropped field makes the
    steps vanish the moment the stream ends."""

    def test_message_out_carries_tool_trace(self) -> None:
        message = SimpleNamespace(
            id=5,
            role="assistant",
            content="回复",
            attachments=[],
            suggestions=[],
            optimized_resume_data=None,
            tool_trace=[{"id": "tc1", "name": "get_resume_context", "label": "查阅简历「实习/工作经历」", "status": "end", "ok": True}],
            pending_questions=None,
            action_status="none",
            create_time=datetime.now(),
        )
        out = _message_out(message)
        self.assertEqual(out.tool_trace, message.tool_trace)


if __name__ == "__main__":
    unittest.main()
