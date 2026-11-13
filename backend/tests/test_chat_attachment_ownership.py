"""对话附件与文件代理的越权防线。

前端传来的 `object_name` 只是路径字符串，不校验归属就等于把对象桶当公开读
接口。这里固定三条规则：
1. 只有 uploaded_files 里 (user_id, object_name) 匹配的对象才算「自己的」；
2. 转发给 AI 服务时只允许读这个白名单内的对象；
3. HTTP 代理对 `ai-chat/` 前缀要求登录且只能读自己的文件，其余前缀保持公开。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.files import router as files_router
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.services import ai_chat_service
from app.services.storage import storage_service
from app.services.storage.storage_service import is_allowed_object_name, is_private_object_name


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _DbStub:
    """只回答归属查询：user_id 命中的对象名才返回。"""

    def __init__(self, owned: set[str]):
        self.owned = owned
        self.queries = 0

    def scalars(self, _stmt):
        self.queries += 1
        return _ScalarResult(self.owned)


class ObjectNameGuardTest(unittest.TestCase):
    def test_rejects_traversal_and_unknown_prefixes(self) -> None:
        self.assertFalse(is_allowed_object_name("../etc/passwd"))
        self.assertFalse(is_allowed_object_name("/avatar/1.png"))
        self.assertFalse(is_allowed_object_name("avatar/../../secret.png"))
        self.assertFalse(is_allowed_object_name("avatar\\1.png"))
        self.assertFalse(is_allowed_object_name("other/1.png"))
        self.assertFalse(is_allowed_object_name(""))

    def test_accepts_business_prefixes(self) -> None:
        self.assertTrue(is_allowed_object_name("avatar/7_ab.png"))
        self.assertTrue(is_allowed_object_name("announcement/ab.png"))
        self.assertTrue(is_allowed_object_name("ai-chat/7_ab.png"))

    def test_ai_chat_is_private(self) -> None:
        self.assertTrue(is_private_object_name("ai-chat/7_ab.png"))
        self.assertFalse(is_private_object_name("avatar/7_ab.png"))

    def test_owned_lookup_matches_on_user_and_object(self) -> None:
        db = _DbStub({"ai-chat/mine.png"})
        owned = storage_service.owned_object_names(db, 7, ["ai-chat/mine.png", "ai-chat/other.png"])

        self.assertEqual(owned, {"ai-chat/mine.png"}, "只返回数据库确认属于本人的对象")
        self.assertEqual(db.queries, 1)

    def test_owned_lookup_skips_the_query_for_empty_input(self) -> None:
        db = _DbStub(set())
        self.assertEqual(storage_service.owned_object_names(db, 7, []), set())
        self.assertEqual(db.queries, 0)


class SanitizeAttachmentsTest(unittest.TestCase):
    def test_drops_foreign_object_name_but_keeps_legacy_url(self) -> None:
        attachments = [
            {"url": "/api/files/ai-chat/mine.png", "object_name": "ai-chat/mine.png", "content_type": "image/png"},
            {"url": "/api/files/ai-chat/other.png", "object_name": "ai-chat/other.png", "content_type": "image/png"},
            # 早期数据只有 url，没有 object_name：不读存储，保留用于展示。
            {"url": "/api/files/ai-chat/legacy.png", "name": "旧图片"},
        ]
        with patch.object(ai_chat_service, "owned_object_names", return_value={"ai-chat/mine.png"}):
            kept = ai_chat_service._sanitize_attachments(_DbStub(set()), 7, attachments)

        self.assertEqual([item["url"] for item in kept], ["/api/files/ai-chat/mine.png", "/api/files/ai-chat/legacy.png"])

    def test_accepts_pydantic_style_attachments(self) -> None:
        class _Model:
            def model_dump(self):
                return {"url": "/api/files/ai-chat/mine.png", "object_name": "ai-chat/mine.png"}

        with patch.object(ai_chat_service, "owned_object_names", return_value={"ai-chat/mine.png"}):
            kept = ai_chat_service._sanitize_attachments(_DbStub(set()), 7, [_Model()])

        self.assertEqual(len(kept), 1)


def _make_client(owned: set[str], user):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(files_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: _DbStub(owned)
    return app


class FileProxyAuthTest(unittest.TestCase):
    def _get(self, path: str, owned: set[str], user, headers=None, params=None):
        app = _make_client(owned, user)
        stream = patch("app.api.files.stream_uploaded_file", return_value=_ok_response())
        user_lookup = patch("app.api.files.user_from_token", return_value=user)
        with stream, user_lookup:
            return TestClient(app).get(path, headers=headers or {}, params=params or {})

    def test_public_prefix_stays_anonymous(self) -> None:
        resp = self._get("/api/files/avatar/7_ab.png", set(), None)
        self.assertEqual(resp.status_code, 200)

    def test_private_prefix_requires_login(self) -> None:
        resp = self._get("/api/files/ai-chat/7_ab.png", {"ai-chat/7_ab.png"}, None)
        self.assertEqual(resp.status_code, 404, "未登录与不存在返回同一状态，避免探测")

    def test_private_prefix_rejects_non_owner(self) -> None:
        resp = self._get("/api/files/ai-chat/7_ab.png", {"ai-chat/other.png"}, SimpleNamespace(id=7))
        self.assertEqual(resp.status_code, 404)

    def test_private_prefix_allows_owner_via_query_token(self) -> None:
        resp = self._get("/api/files/ai-chat/7_ab.png", {"ai-chat/7_ab.png"}, SimpleNamespace(id=7), params={"token": "t"})
        self.assertEqual(resp.status_code, 200)

    def test_unknown_prefix_is_not_proxied(self) -> None:
        resp = self._get("/api/files/secret/7_ab.png", {"secret/7_ab.png"}, SimpleNamespace(id=7), params={"token": "t"})
        self.assertEqual(resp.status_code, 404)


def _ok_response():
    from fastapi.responses import Response

    return Response(content=b"png", media_type="image/png")


if __name__ == "__main__":
    unittest.main()
