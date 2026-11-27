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

    def setUp(self) -> None:
        # 默认个人信息要查库；这些用例只验证上下文字段，统一当作「没有信息库」。
        patcher = patch("app.services.ai_chat_service.resolve_default_basic", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_context_injects_default_basic_and_placeholder_flags(self) -> None:
        """基本信息不能只靠模型「想起来检索」：默认个人信息要随上下文下发。"""
        resume = self._resume()
        resume.resume_data["basics"] = {
            "name": "Elliot",
            "email": "zhangsan@example.com",
            "phone": "13800000000",
            "avatar": "",
        }
        row = SimpleNamespace(
            id=62,
            title="卢俊隆",
            payload={
                "name": "卢俊隆",
                "email": "lujunlong631@163.com",
                "avatar": "avatar/1_x.jpg",
                "phone": "",
            },
        )
        with (
            patch("app.services.ai_chat_service.list_chat_messages", return_value=[]),
            patch("app.services.ai_chat_service.resolve_default_basic", return_value=row),
        ):
            context = build_ai_service_context(_DbStub(pending=None), SimpleNamespace(id=1), resume)

        self.assertEqual(context["known_basics"]["fragment_id"], 62)
        self.assertEqual(context["known_basics"]["payload"]["name"], "卢俊隆")
        # 头像对象名不是可写进简历的事实；空值也不下发，避免提示词里出现空字段
        self.assertNotIn("avatar", context["known_basics"]["payload"])
        self.assertNotIn("phone", context["known_basics"]["payload"])
        self.assertTrue(context["basics_looks_like_template"])
        self.assertIn("name", context["basics_placeholder_fields"])
        self.assertIn("email", context["basics_placeholder_fields"])


if __name__ == "__main__":
    unittest.main()
