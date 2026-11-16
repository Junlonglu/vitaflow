"""上传入口与文件代理的加固点。

代理的鉴权边界已有 ``test_chat_attachment_ownership`` 覆盖，这里补两块原来完全
没有断言的部分：

1. 上传入口只收白名单图片类型、各自的大小上限，并且对象名强制带业务前缀 +
   用户 id（对象名是后面「是不是你的」唯一凭据）；
2. 代理响应头：「对话图片」是鉴权后才返回的，不能带 public 缓存出门，否则共享
   缓存会把一份已鉴权响应发给下一个请求；同时禁止浏览器嗅探。
"""
from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi import UploadFile

from app.services.storage import storage_service as svc


class _Db:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


def _upload(content: bytes, content_type: str | None, filename: str = "a.png") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=BytesIO(content),
        headers={"content-type": content_type or ""},
    )


class _FakeStorage:
    """拦住真实 MinIO/OSS 写入，只记录对象名。"""

    def __init__(self):
        self.puts: list[str] = []

    def __call__(self, object_name, content, content_type):
        self.puts.append(object_name)


def _patched_storage() -> tuple:
    fake = _FakeStorage()
    return (
        patch.object(svc, "_provider", return_value="minio"),
        patch.object(svc, "_upload_minio", side_effect=fake),
        patch.object(svc, "_file_url", side_effect=lambda name: f"/api/files/{name}"),
    )


class AllowedTypesTest(unittest.TestCase):
    def test_rejects_non_image_content_types(self):
        for content_type in ("text/html", "image/svg+xml", "application/pdf", "", None):
            with self.subTest(content_type=content_type):
                with self.assertRaises(Exception) as ctx:
                    svc.upload_avatar(_Db(), 7, _upload(b"x", content_type))
                self.assertEqual(getattr(ctx.exception, "code", None), 400)

    def test_rejects_oversized_avatar(self):
        with self.assertRaises(Exception) as ctx:
            svc.upload_avatar(_Db(), 7, _upload(b"x" * (svc.MAX_SIZE + 1), "image/png"))
        self.assertEqual(getattr(ctx.exception, "code", None), 400)

    def test_ai_chat_image_limit_is_larger_than_avatar(self):
        # 5MB 的对话图片应当通过，头像则不允许（3MB 上限）。
        content = b"x" * (svc.MAX_SIZE + 1)
        patches = _patched_storage()
        with patches[0], patches[1], patches[2]:
            svc.upload_ai_chat_image(_Db(), 7, _upload(content, "image/png"))
        with self.assertRaises(Exception):
            svc.upload_avatar(_Db(), 7, _upload(content, "image/png"))


class ObjectNameTest(unittest.TestCase):
    def test_avatar_object_name_carries_owner_and_suffix(self):
        db = _Db()
        first, second, third = _patched_storage()
        with first, second, third:
            result = svc.upload_avatar(db, 42, _upload(b"png", "image/png"))

        self.assertTrue(result["object_name"].startswith("avatar/42_"), result["object_name"])
        self.assertTrue(result["object_name"].endswith(".png"))
        self.assertEqual(db.added[0].user_id, 42)
        self.assertEqual(db.added[0].object_name, result["object_name"])
        self.assertEqual(db.added[0].file_type, "avatar")
        self.assertTrue(db.committed)

    def test_ai_chat_object_name_carries_owner(self):
        db = _Db()
        first, second, third = _patched_storage()
        with first, second, third:
            result = svc.upload_ai_chat_image(db, 9, _upload(b"png", "image/webp"))

        self.assertTrue(result["object_name"].startswith("ai-chat/9_"))
        self.assertTrue(result["object_name"].endswith(".webp"))

    def test_announcement_object_name_has_no_owner(self):
        # 公告图对外公开，对象名里不带 user id。
        db = _Db()
        first, second, third = _patched_storage()
        with first, second, third:
            result = svc.upload_announcement_image(db, 3, _upload(b"png", "image/png"))
        tail = result["object_name"].split("announcement/", 1)[1]
        self.assertFalse(tail.startswith("3_"), tail)

    def test_every_uploaded_object_passes_the_proxy_allowlist(self):
        # 写进去的对象名必须能过代理的白名单，否则上传成功但页面读不出来。
        for fn, content_type in (
            (svc.upload_avatar, "image/png"),
            (svc.upload_ai_chat_image, "image/jpeg"),
            (svc.upload_announcement_image, "image/webp"),
        ):
            with self.subTest(fn.__name__):
                first, second, third = _patched_storage()
                with first, second, third:
                    result = fn(_Db(), 5, _upload(b"png", content_type))
                self.assertTrue(svc.is_allowed_object_name(result["object_name"]), result["object_name"])


class StreamHeaderTest(unittest.TestCase):
    def test_private_object_is_not_publicly_cacheable(self):
        headers = svc._stream_cache_headers("ai-chat/7_ab.png")
        self.assertIn("private", headers["Cache-Control"])
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertNotIn("public", headers["Cache-Control"])

    def test_public_object_keeps_long_cache(self):
        headers = svc._stream_cache_headers("avatar/7_ab.png")
        self.assertIn("public", headers["Cache-Control"])

    def test_both_disable_sniffing(self):
        for name in ("avatar/7_ab.png", "ai-chat/7_ab.png"):
            with self.subTest(name=name):
                self.assertEqual(svc._stream_cache_headers(name)["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
