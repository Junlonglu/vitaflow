import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.resume import Resume
from app.services.ai_chat_service import build_ai_service_context


class _PendingStub:
    id = 42
    suggestions = ["建议一", "建议二"]


class _DbStub:
    """Minimal session stand-in: only needs scalar() to return the pending change."""

    def __init__(self, pending=None) -> None:
        self._pending = pending

    def scalar(self, *args, **kwargs):
        return self._pending


class AiServiceContextBuildTest(unittest.TestCase):
    """Verify build_ai_service_context assembles the injected context from MySQL.

    Pure unit test: list_chat_messages is patched and the session is a stub, so no
    SQLite/MySQL is required (the local venv cannot load _sqlite3).
    """

    def _resume(self) -> Resume:
        return Resume(
            id=7,
            user_id=1,
            title="后端工程师简历",
            resume_data={
                "basics": {"name": "张三", "title": "后端工程师"},
                "skills": [{"name": "Python"}],
                "work": [{"company": "某厂"}],
                "custom_sections": [{"id": "sec1", "title": "荣誉"}],
            },
            template_config={},
        )

    def _history(self, *items: tuple[str, str]) -> list[SimpleNamespace]:
        return [SimpleNamespace(role=role, content=text) for role, text in items]

    def test_context_includes_resume_snapshot_history_and_pending(self) -> None:
        resume = self._resume()
        history = self._history(("user", "帮我看看简历"), ("assistant", "好的，你希望重点优化什么？"))
        with patch(
            "app.services.ai_chat_service.list_chat_messages", return_value=history
        ) as mock_list:
            context = build_ai_service_context(_DbStub(pending=None), SimpleNamespace(id=1), resume)

        # 简历全文随上下文下发（Agent 不必再读一次），字段清单单独给一行一模块
        self.assertEqual(context["resume"]["id"], 7)
        self.assertEqual(context["resume"]["title"], "后端工程师简历")
        self.assertEqual(context["resume"]["data"]["basics"]["name"], "张三")
        self.assertIn("work", context["resume"]["data"])
        self.assertIn("work:", context["resume_fields"])
        self.assertIn("highlights", context["resume_fields"])
        self.assertNotIn("resume_schema", context)
        # Recent history, clipped to the configured limit
        self.assertEqual(
            context["history"],
            [
                {"role": "user", "content": "帮我看看简历"},
                {"role": "assistant", "content": "好的，你希望重点优化什么？"},
            ],
        )
        # Pending change summary shape, none outstanding
        self.assertEqual(context["pending_change"], {"exists": False, "message_id": None, "summary": []})
        mock_list.assert_called_once()

    def test_context_includes_outstanding_pending_change(self) -> None:
        resume = self._resume()
        with patch("app.services.ai_chat_service.list_chat_messages", return_value=[]):
            context = build_ai_service_context(_DbStub(pending=_PendingStub()), SimpleNamespace(id=1), resume)

        self.assertEqual(context["pending_change"], {"exists": True, "message_id": 42, "summary": ["建议一", "建议二"]})

    def test_context_clips_history_to_limit(self) -> None:
        resume = self._resume()
        history = self._history(
            ("user", "第一轮"),
            ("assistant", "回复一"),
            ("user", "第二轮"),
            ("assistant", "回复二"),
            ("user", "第三轮"),
        )
        with patch("app.services.ai_chat_service.list_chat_messages", return_value=history):
            context = build_ai_service_context(
                _DbStub(pending=None), SimpleNamespace(id=1), resume, limit=2
            )

        # Only the two most recent history entries are injected.
        self.assertEqual(len(context["history"]), 2)
        self.assertEqual(context["history"][0], {"role": "assistant", "content": "回复二"})
        self.assertEqual(context["history"][1], {"role": "user", "content": "第三轮"})


if __name__ == "__main__":
    unittest.main()
