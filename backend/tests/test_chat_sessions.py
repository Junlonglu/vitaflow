"""会话命名规则：未命名会话由首条用户消息自动起标题。

DB 打桩（本地 venv 无法加载 sqlite），只验证判定逻辑本身：什么情况下改名、
什么情况下必须保留用户起的名字。
"""
import unittest
from types import SimpleNamespace

from app.services.ai_chat_service import (
    _AUTO_SESSION_TITLE_LENGTH,
    _auto_session_title,
    _maybe_apply_auto_title,
)


class _DbStub:
    """scalar() 只被用来数「已有几个用户回合」。"""

    def __init__(self, user_turns: int = 0) -> None:
        self.user_turns = user_turns

    def scalar(self, *_args, **_kwargs):
        return self.user_turns


class AutoSessionTitleTest(unittest.TestCase):
    def test_content_is_collapsed_and_trimmed(self) -> None:
        self.assertEqual(_auto_session_title("  按 JD\n  优化  "), "按 JD 优化")

    def test_long_content_is_clipped_with_ellipsis(self) -> None:
        title = _auto_session_title("把" * 40)
        self.assertEqual(len(title), _AUTO_SESSION_TITLE_LENGTH + 1)
        self.assertTrue(title.endswith("…"))

    def test_blank_content_keeps_session_untitled(self) -> None:
        session = SimpleNamespace(id=1, title="新对话")
        self.assertFalse(_maybe_apply_auto_title(_DbStub(0), session, "   "))
        self.assertEqual(session.title, "新对话")

    def test_first_user_message_names_the_session(self) -> None:
        session = SimpleNamespace(id=1, title="新对话")
        self.assertTrue(_maybe_apply_auto_title(_DbStub(0), session, "帮我改一版项目经历"))
        self.assertEqual(session.title, "帮我改一版项目经历")

    def test_legacy_default_title_is_treated_as_untitled(self) -> None:
        # 多会话改造前的旧会话都叫「AI 简历助手」，首条新消息仍应给它起名。
        session = SimpleNamespace(id=1, title="AI 简历助手")
        self.assertTrue(_maybe_apply_auto_title(_DbStub(0), session, "按字节 JD 改一版"))

    def test_renamed_session_is_never_overwritten(self) -> None:
        session = SimpleNamespace(id=1, title="按字节 JD 改一版")
        self.assertFalse(_maybe_apply_auto_title(_DbStub(0), session, "再问一个"))
        self.assertEqual(session.title, "按字节 JD 改一版")

    def test_session_with_history_is_never_renamed(self) -> None:
        session = SimpleNamespace(id=1, title="新对话")
        self.assertFalse(_maybe_apply_auto_title(_DbStub(2), session, "再问一个"))
        self.assertEqual(session.title, "新对话")


if __name__ == "__main__":
    unittest.main()
