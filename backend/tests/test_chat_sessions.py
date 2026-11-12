"""会话命名 / 搜索 / 置顶相关的纯逻辑单测。

DB 打桩（本地 venv 无法加载 sqlite），只验证判定逻辑本身：什么情况下改名、
什么情况下必须保留用户起的名字、搜索关键词如何转义。
"""
import unittest
from datetime import datetime
from types import SimpleNamespace

from app.services.ai_chat_service import (
    _AUTO_SESSION_TITLE_LENGTH,
    _CHAT_SEARCH_KEYWORD_LENGTH,
    _auto_session_title,
    _maybe_apply_auto_title,
    _search_like_pattern,
    chat_session_data,
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


class SessionSearchPatternTest(unittest.TestCase):
    def test_blank_keyword_matches_nothing(self) -> None:
        self.assertEqual(_search_like_pattern("   "), "")
        self.assertEqual(_search_like_pattern(None), "")

    def test_whitespace_is_collapsed(self) -> None:
        self.assertEqual(_search_like_pattern("  按 JD\n 优化 "), "%按 JD 优化%")

    def test_like_wildcards_are_escaped(self) -> None:
        # 不转义的话 "%" 会匹配全部会话，"_" 会变成任意单字符。
        self.assertEqual(_search_like_pattern("100%"), "%100\\%%")
        self.assertEqual(_search_like_pattern("a_b"), "%a\\_b%")

    def test_backslash_is_escaped_first(self) -> None:
        # 反斜杠必须先于 % / _ 转义，否则会把后面的转义符吞掉。
        self.assertEqual(_search_like_pattern("a\\b"), "%a\\\\b%")

    def test_long_keyword_is_clipped(self) -> None:
        pattern = _search_like_pattern("啊" * 200)
        self.assertEqual(pattern, f"%{'啊' * _CHAT_SEARCH_KEYWORD_LENGTH}%")


class SessionPayloadTest(unittest.TestCase):
    def _session(self, **overrides):
        base = dict(
            id=7,
            resume_id=3,
            title="优化项目经历",
            is_pinned=True,
            create_time=datetime(2026, 11, 12, 10, 0),
            update_time=datetime(2026, 11, 12, 11, 0),
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_pin_flag_is_exposed(self) -> None:
        self.assertTrue(chat_session_data(self._session())["is_pinned"])
        self.assertFalse(chat_session_data(self._session(is_pinned=False))["is_pinned"])

    def test_legacy_row_without_pin_column_defaults_to_false(self) -> None:
        session = self._session()
        del session.is_pinned
        self.assertFalse(chat_session_data(session)["is_pinned"])

    def test_blank_title_falls_back_to_placeholder(self) -> None:
        self.assertEqual(chat_session_data(self._session(title=""))["title"], "新对话")


if __name__ == "__main__":
    unittest.main()
