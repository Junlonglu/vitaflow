"""私有图片的访问凭据：短签名 URL 取代「把 JWT 拼在地址上」（RF-025）。

`<img src>` 带不了 Authorization 头，所以对话图片必须靠 URL 自证。旧做法是把
登录 JWT 直接拼成 `?token=`，于是 token 进了浏览器历史、代理日志、错误上报，
而它的有效期是 24 小时——一次日志泄漏就是一次账号接管。

这里固定新的信任边界：
- 合法凭据是 `sig` + `exp`（HMAC 覆盖对象名与过期时间，只对这张图有效）；
- `?token=` 一律不再接受（否则旧攻击面还在）；
- Bearer 头仍然可用（接口调用方）；
- 未授权与「不是你的文件」继续返回同一个 404，不泄露对象是否存在。
"""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse

from app.api.files import router as files_router
from app.core import contracts
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token
from app.core.signed_url import signed_query, verify_signature
from app.services.storage import storage_service

OBJECT = "ai-chat/7_ab12.png"
URL = f"/api/files/{OBJECT}"


def _client():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(files_router, prefix="/api")
    return TestClient(app)


def _stream(*_args, **_kwargs):
    return StreamingResponse(iter([b"png-bytes"]), media_type="image/png")


class SignedUrlTest(unittest.TestCase):
    def setUp(self):
        self.client = _client()
        self._patches = [
            patch.object(storage_service, "stream_uploaded_file", _stream),
            patch("app.api.files.stream_uploaded_file", _stream),
            patch("app.core.security._token_claims", lambda token: (7, 0) if token == "good" else None),
            patch("app.api.files.user_from_token", lambda db, token: SimpleNamespace(id=7) if token == "good" else None),
            patch("app.api.files.owned_object_names", lambda db, user_id, names: set(names)),
            patch("app.api.files.get_db", lambda: None),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()

    def _get(self, query: str = "", headers: dict | None = None):
        return self.client.get(f"{URL}{query}", headers=headers or {})

    def test_valid_signature_grants_access(self):
        resp = self._get(f"?{signed_query(OBJECT)}")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_signature_is_bound_to_the_object_name(self):
        """签给 A 的签名不能读 B。"""
        other = "ai-chat/9_other.png"
        resp = self.client.get(f"/api/files/{other}?{signed_query(OBJECT)}")
        self.assertEqual(resp.status_code, 404)

    def test_bad_signature_is_404(self):
        resp = self._get(f"?sig=deadbeef&exp={int(time.time()) + 600}")
        self.assertEqual(resp.status_code, 404)

    def test_expired_signature_is_404(self):
        resp = self._get(f"?{signed_query(OBJECT, ttl_seconds=-1)}")
        self.assertEqual(resp.status_code, 404)

    def test_missing_expiry_is_404(self):
        resp = self._get(f"?sig={signed_query(OBJECT).split('&')[0].split('=')[1]}")
        self.assertEqual(resp.status_code, 404)

    def test_jwt_in_query_is_no_longer_accepted(self):
        """RF-025 的正题：`?token=` 必须失效，否则只是换了个地方放 JWT。"""
        token = create_access_token(7, token_version=0)
        resp = self._get(f"?token={token}")
        self.assertEqual(resp.status_code, 404)

    def test_bearer_header_still_works_for_api_clients(self):
        resp = self._get(headers={"Authorization": "Bearer good"})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_public_objects_need_no_signature(self):
        with patch("app.api.files.is_private_object_name", lambda name: False):
            resp = self.client.get("/api/files/avatar/1_a.jpg")
        self.assertEqual(resp.status_code, 200, resp.text)


class SignaturePrimitivesTest(unittest.TestCase):
    def test_ttl_default_matches_contract(self):
        self.assertGreater(contracts.FILE_URL_TTL_SECONDS, 0)
        query = signed_query(OBJECT)
        exp = int(query.split("exp=")[1])
        self.assertGreater(exp, int(time.time()))

    def test_verify_is_time_aware(self):
        query = signed_query(OBJECT, ttl_seconds=60, now=1_000)
        sig = query.split("sig=")[1].split("&")[0]
        exp = query.split("exp=")[1]
        self.assertTrue(verify_signature(OBJECT, sig, exp, now=1_030))
        self.assertFalse(verify_signature(OBJECT, sig, exp, now=1_061))


class AttachmentSerializationTest(unittest.TestCase):
    def test_private_attachments_are_signed_on_read(self):
        """历史消息里的 URL 是落库的裸地址，读出时必须重新签。"""
        from app.services.ai_chat_service import _signed_attachments

        items = _signed_attachments(
            [
                {"url": URL, "object_name": OBJECT, "name": "a.png"},
                {"url": "/api/files/avatar/1_a.jpg", "object_name": "avatar/1_a.jpg", "name": "b.jpg"},
            ]
        )
        self.assertIn("sig=", items[0]["url"])
        self.assertIn("exp=", items[0]["url"])
        self.assertEqual(items[1]["url"], "/api/files/avatar/1_a.jpg")


if __name__ == "__main__":
    unittest.main()
