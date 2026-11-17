"""头像库：账号级素材的识别、清单、引用统计与删除语义。

头像不再存在简历里，而是复用 ``uploaded_files``（``file_type="avatar"``）当库，
简历侧只留对象名。这里固定三件事：三种历史形态都能还原成对象名、引用计数按
``user_id`` 隔离、删除只清索引不删对象（否则已分享的简历会当场裂图）。
"""
from __future__ import annotations

import io
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.avatars import router as avatars_router
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.core.security import get_current_user
from app.models.resume import Resume, UploadedFile
from app.services import avatar_service as svc
from app.services.preview_service import render_resume_html
from app.services.storage.storage_service import MAX_AVATARS_PER_USER, upload_avatar


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


_NEXT_ID = {"value": 1}


def _add_avatar(db, user_id=1, object_name="avatar/1_a.jpg", file_name="a.jpg"):
    # uploaded_files 的主键是 BigInteger，SQLite 上不自增，测试里显式给 id。
    row = UploadedFile(
        id=_NEXT_ID["value"],
        user_id=user_id,
        file_type="avatar",
        file_name=file_name,
        object_name=object_name,
        file_url=f"http://127.0.0.1:8000/api/files/{object_name}",
        content_type="image/jpeg",
        file_size=1024,
    )
    _NEXT_ID["value"] += 1
    db.add(row)
    db.commit()
    return row


def _add_resume(db, avatar, user_id=1):
    row = Resume(
        id=_NEXT_ID["value"],
        user_id=user_id,
        title="简历",
        language="zh-CN",
        template_id="tech",
        template_config={},
        resume_data={"basics": {"avatar": avatar}},
    )
    _NEXT_ID["value"] += 1
    db.add(row)
    db.commit()
    return row


class AvatarObjectNameTest(unittest.TestCase):
    def test_accepts_object_name_proxy_url_and_public_url(self):
        self.assertEqual(svc.avatar_object_name("avatar/1_a.jpg"), "avatar/1_a.jpg")
        self.assertEqual(
            svc.avatar_object_name(f"{settings.pdf_base_url.rstrip('/')}/api/files/avatar/1_a.jpg"),
            "avatar/1_a.jpg",
        )
        self.assertEqual(
            svc.avatar_object_name(f"{settings.minio_public_url.rstrip('/')}/avatar/1_a.jpg"),
            "avatar/1_a.jpg",
        )

    def test_rejects_external_and_non_avatar_values(self):
        for value in ("https://cdn.example.com/me.png", "data:image/png;base64,AAA", "", None):
            with self.subTest(value=value):
                self.assertEqual(svc.avatar_object_name(value), "")
        # 白名单只管 avatar/：公告图地址混进头像字段时不该被当成头像。
        self.assertEqual(svc.avatar_object_name("/api/files/announcement/x.jpg"), "")

    def test_rejects_path_traversal(self):
        self.assertEqual(svc.avatar_object_name("avatar/../secret"), "")

    def test_resolve_for_render_keeps_unknown_values(self):
        external = "https://cdn.example.com/me.png"
        self.assertEqual(svc.resolve_for_render(external), external)
        self.assertEqual(svc.resolve_for_render(""), "")
        self.assertEqual(
            svc.resolve_for_render("avatar/1_a.jpg"),
            f"{settings.pdf_base_url.rstrip('/')}/api/files/avatar/1_a.jpg",
        )


class AvatarRenderTest(unittest.TestCase):
    def test_preview_rewrites_object_name_to_absolute_url(self):
        html = render_resume_html(
            {"basics": {"name": "张三", "avatar": "avatar/1_a.jpg"}},
            "classic",
            {"show_avatar": True},
            "zh-CN",
        )
        self.assertIn(f"{settings.pdf_base_url.rstrip('/')}/api/files/avatar/1_a.jpg", html)


class AvatarLibraryTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

    def test_list_is_per_user_and_newest_first(self):
        _add_avatar(self.db, 1, "avatar/1_old.jpg", "old.jpg")
        _add_avatar(self.db, 1, "avatar/1_new.jpg", "new.jpg")
        _add_avatar(self.db, 2, "avatar/2_other.jpg", "other.jpg")
        listed = svc.list_avatars(self.db, 1)
        self.assertEqual([item["object_name"] for item in listed["items"]], ["avatar/1_new.jpg", "avatar/1_old.jpg"])
        self.assertEqual(listed["limit"], MAX_AVATARS_PER_USER)
        # 前端展示用相对地址：开发走 Vite 代理、生产走同源 Nginx。
        self.assertEqual(listed["items"][0]["url"], "/api/files/avatar/1_new.jpg")

    def test_usage_counts_only_own_resumes(self):
        _add_avatar(self.db, 1, "avatar/1_used.jpg")
        _add_avatar(self.db, 1, "avatar/1_legacy.jpg")
        _add_resume(self.db, "avatar/1_used.jpg")
        _add_resume(self.db, "avatar/1_used.jpg")
        # 旧数据存的是完整代理 URL，也要能归到同一张头像上。
        _add_resume(self.db, f"{settings.pdf_base_url.rstrip('/')}/api/files/avatar/1_legacy.jpg")
        _add_resume(self.db, "avatar/1_used.jpg", user_id=2)
        counts = svc.usage_counts(self.db, 1)
        self.assertEqual(counts["avatar/1_used.jpg"], 2)
        self.assertEqual(counts["avatar/1_legacy.jpg"], 1)
        listed = {item["object_name"]: item for item in svc.list_avatars(self.db, 1)["items"]}
        self.assertEqual(listed["avatar/1_used.jpg"]["usage_count"], 2)

    def test_delete_removes_index_only(self):
        row = _add_avatar(self.db)
        svc.delete_avatar(self.db, 1, row.id)
        self.assertIsNone(self.db.query(UploadedFile).filter(UploadedFile.id == row.id).first())

    def test_delete_ignores_other_users_row(self):
        row = _add_avatar(self.db, 2, "avatar/2_other.jpg")
        svc.delete_avatar(self.db, 1, row.id)
        # 跨用户删除当作不存在：不能借 id 猜出别人有没有这张图。
        self.assertIsNotNone(self.db.query(UploadedFile).filter(UploadedFile.id == row.id).first())


class AvatarUploadCapTest(unittest.TestCase):
    def test_upload_rejects_beyond_limit(self):
        db = _session()
        for index in range(MAX_AVATARS_PER_USER):
            _add_avatar(db, 1, f"avatar/1_{index}.jpg")
        fake = SimpleNamespace(content_type="image/jpeg", file=io.BytesIO(b"x"), filename="a.jpg")
        with self.assertRaises(AppException) as ctx:
            upload_avatar(db, 1, fake)
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("删除", ctx.exception.message)


class AvatarRouteTest(unittest.TestCase):
    def setUp(self):
        self.db = _session()

        def override_db():
            yield self.db

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(avatars_router, prefix="/api")
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        self.client = TestClient(app)

    def test_list_returns_own_avatars(self):
        _add_avatar(self.db, 1, "avatar/1_mine.jpg")
        _add_avatar(self.db, 2, "avatar/2_other.jpg")
        body = self.client.get("/api/avatars").json()
        self.assertEqual([item["object_name"] for item in body["data"]["items"]], ["avatar/1_mine.jpg"])

    def test_delete_other_users_avatar_is_noop(self):
        row = _add_avatar(self.db, 2, "avatar/2_other.jpg")
        resp = self.client.delete(f"/api/avatars/{row.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(self.db.query(UploadedFile).filter(UploadedFile.id == row.id).first())


if __name__ == "__main__":
    unittest.main()
