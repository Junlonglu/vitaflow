"""后台富文本设置项的 XSS 防护。

``user_agreement`` 会出现在未登录的登录页、``ai_records_hint`` 会出现在 AI 记录页，
两者前端都走 ``v-html``。公告与反馈都过了 bleach 白名单，设置项曾经漏了这一步。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.ai_config import AppSetting
from app.services import app_settings_service as svc


class _DbStub:
    """只模拟「按 key 取一行」和「取全部行」两种用法。

    ``ensure_default_settings`` 在 setUp 里被 patch 掉：它负责补种默认值，与
    这里的清理语义无关，留着反而会覆盖 stub 里的脏数据。
    """

    def __init__(self, item: AppSetting | None = None) -> None:
        self.item = item
        self.commits = 0

    def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: [self.item] if self.item else [])

    def scalar(self, _stmt):
        return self.item

    def add(self, obj):
        self.item = obj

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, _obj) -> None:
        pass


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return 1


class SettingSanitizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = _FakeRedis()
        for patcher in (
            patch.object(svc, "redis_client", self.redis),
            patch.object(svc, "ensure_default_settings"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_write_strips_executable_markup(self) -> None:
        db = _DbStub()
        payload = '<p onclick="alert(1)">协议</p><script>alert(2)</script><a href="javascript:alert(3)">点我</a>'

        item = svc.set_setting(db, "user_agreement", payload, "用户协议")

        self.assertNotIn("script", item.value.lower())
        self.assertNotIn("onclick", item.value.lower())
        self.assertNotIn("javascript:", item.value.lower())
        self.assertIn("<p>协议</p>", item.value)

    def test_markup_is_kept_for_rich_text_settings(self) -> None:
        db = _DbStub()

        item = svc.set_setting(db, "user_agreement", "<p><strong>加粗</strong>正文</p>")

        self.assertIn("<strong>加粗</strong>", item.value)

    def test_ai_records_hint_is_also_treated_as_rich_text(self) -> None:
        db = _DbStub()

        item = svc.set_setting(db, "ai_records_hint", '<img src=x onerror="alert(1)">1 元约 100 点')

        self.assertNotIn("onerror", item.value.lower())
        self.assertIn("1 元约 100 点", item.value)

    def test_plain_settings_are_not_rewritten(self) -> None:
        db = _DbStub()

        item = svc.set_setting(db, "feedback_notify_email", "ops@example.com")

        self.assertEqual(item.value, "ops@example.com", "非富文本项不该被包成 <p>")

    def test_read_sanitizes_legacy_stored_html(self) -> None:
        dirty = AppSetting(key="user_agreement", value='<svg onload="alert(1)"></svg><p>历史内容</p>')
        db = _DbStub(dirty)

        value = svc.get_setting(db, "user_agreement")

        self.assertEqual(value, "<p>历史内容</p>")

    def test_cached_value_is_sanitized_on_the_way_out(self) -> None:
        # 修复前写进 Redis 的缓存同样不能直接回给前端。
        self.redis.store[svc._cache_key("ai_records_hint")] = '<script>alert(1)</script>提示'
        db = _DbStub()

        value = svc.get_setting(db, "ai_records_hint")

        self.assertNotIn("script", value.lower())
        self.assertIn("提示", value)

    def test_admin_view_returns_sanitized_values(self) -> None:
        dirty = AppSetting(key="user_agreement", value='<iframe src="//evil"></iframe><p>正文</p>')
        db = _DbStub(dirty)

        data = svc.get_admin_settings(db)

        self.assertNotIn("iframe", data["user_agreement"].lower())
        self.assertEqual(data["user_agreement"], "<p>正文</p>")

    def test_default_hint_text_survives_sanitization(self) -> None:
        db = _DbStub()

        value = svc.get_setting(db, "ai_records_hint")

        self.assertIn("Flow Points", value)


if __name__ == "__main__":
    unittest.main()
