"""简历版本历史：写入去重、保留策略、恢复与读取接口。

分两层覆盖：
1. service 层用严格的 stub 库验证「内容指纹去重 / 手动存档不被清理 /
   恢复前自动备份 / 乐观锁 409 / 回读不一致回滚」这些只在特定写入顺序下
   才会触发的分支；
2. 路由层断言响应形状（列表不带 resume_data）、越权 404、预览用版本自己的
   模板、恢复透传 expected_update_time。
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.resumes import router as resumes_router
from app.core.database import get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.core.security import get_current_user
from app.models.resume import Resume, ResumeVersion
from app.services import resume_service


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _NoopQuery:
    """prune_versions 里的批量删除：只记录，不真的动数据。"""

    def __init__(self, deleted):
        self.deleted = deleted

    def filter(self, *args, **kwargs):
        return self

    def delete(self, **kwargs):
        self.deleted.append(True)
        return 0


class _VersionDb:
    def __init__(self, resume, versions=None, total=None, corrupt_on_expire=False):
        self.resume = resume
        self.versions = list(versions or [])
        self.total = total if total is not None else len(self.versions)
        self.corrupt_on_expire = corrupt_on_expire
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def get(self, model, key):
        if model is Resume:
            return self.resume if key == self.resume.id else None
        for version in [*self.versions, *self.added]:
            if version.id == key:
                return version
        return None

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 1000 + len(self.added)
        self.added.append(obj)

    def scalars(self, _stmt):
        rows = sorted([*self.versions, *self.added], key=lambda item: item.id or 0, reverse=True)
        return _Rows(rows)

    def scalar(self, _stmt):
        return self.total

    def execute(self, _stmt):
        return _Rows(self.versions)

    def query(self, _model):
        return _NoopQuery([])

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _obj):
        pass

    def expire(self, obj, _attrs):
        if self.corrupt_on_expire:
            self.resume.resume_data = {"basics": {"name": "写入后被截断"}}


def _resume(**overrides):
    payload = {
        "id": 7,
        "user_id": 1,
        "language": "zh-CN",
        "resume_data": {"basics": {"name": "当前版本"}},
        "template_id": "tech",
        "template_config": {"template_id": "tech", "theme_color": "#2563eb"},
        "update_time": datetime(2026, 1, 1, 12, 0, 0),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _version(version_id=5, resume_id=7, name="旧版", **overrides):
    payload = {
        "id": version_id,
        "resume_id": resume_id,
        "resume_data": {"basics": {"name": name}},
        "template_config": {"template_id": "classic", "theme_color": "#111111"},
        "reason": "手动保存版本",
        "source": resume_service.VERSION_SOURCE_MANUAL,
        "content_hash": None,
        "size_bytes": 42,
        "create_time": datetime(2026, 1, 1, 11, 0, 0),
    }
    payload.update(overrides)
    return ResumeVersion(**payload)


class CreateVersionTest(unittest.TestCase):
    def test_snapshot_records_source_digest_and_size(self):
        db = _VersionDb(_resume())

        version, created = resume_service.create_version(db, 1, 7, "投递前存档")

        self.assertTrue(created)
        self.assertEqual(version.source, resume_service.VERSION_SOURCE_MANUAL)
        self.assertEqual(len(version.content_hash), 64)
        self.assertGreater(version.size_bytes, 0)
        self.assertEqual(db.commits, 1)

    def test_identical_content_reuses_the_previous_row(self):
        resume = _resume()
        db = _VersionDb(resume)
        first, _ = resume_service.create_version(db, 1, 7, "第一次")
        resume.resume_data = {"basics": {"name": "当前版本"}}

        second, created = resume_service.create_version(db, 1, 7, "重复保存")

        self.assertFalse(created, "内容没变就不该再写一行")
        self.assertEqual(second, first)
        self.assertEqual(len(db.added), 1)

    def test_manual_versions_are_never_pruned(self):
        """超出上限时只回收自动版本，手动存档必须留着。"""
        stale_ids = [3, 4]
        deleted_filters = []

        class _Query:
            def filter(self, *args):
                return self

            def delete(self, **kwargs):
                return len(stale_ids)

        class _PruneDb:
            def scalar(self, _stmt):
                return resume_service.VERSION_KEEP_LIMIT + 2

            def scalars(self, _stmt):
                return _Rows(stale_ids)

            def query(self, _model):
                deleted_filters.append(True)
                return _Query()

        removed = resume_service.prune_versions(_PruneDb(), 7)

        self.assertEqual(removed, len(stale_ids))
        self.assertTrue(deleted_filters)


class RestoreVersionTest(unittest.TestCase):
    def test_restore_backs_up_current_state_then_writes_the_version_back(self):
        db = _VersionDb(_resume(), [_version()])

        restored = resume_service.restore_version(db, 1, 7, 5)

        self.assertEqual(restored.resume_data["basics"]["name"], "旧版")
        self.assertEqual(restored.template_id, "classic", "模板也要跟着回到当时的样子")
        backups = [item for item in db.added if isinstance(item, ResumeVersion)]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].source, resume_service.VERSION_SOURCE_RESTORE)
        self.assertEqual(backups[0].reason, resume_service.VERSION_REASON_RESTORE)
        self.assertEqual(backups[0].resume_data["basics"]["name"], "当前版本", "备份的是恢复前的内容")

    def test_stale_update_time_is_rejected(self):
        db = _VersionDb(_resume(), [_version()])

        with self.assertRaises(AppException) as ctx:
            resume_service.restore_version(db, 1, 7, 5, expected_update_time=datetime(2026, 1, 1, 11, 59, 59))

        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual([item for item in db.added if isinstance(item, ResumeVersion)], [])

    def test_readback_mismatch_rolls_back(self):
        db = _VersionDb(_resume(), [_version()], corrupt_on_expire=True)

        with self.assertRaises(AppException) as ctx:
            resume_service.restore_version(db, 1, 7, 5)

        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(db.rollbacks, 1)

    def test_timezone_aware_update_time_does_not_crash(self):
        """带时区的 expected_update_time 只截秒比较，不能变成 TypeError→500。"""
        db = _VersionDb(_resume(), [_version()])

        restored = resume_service.restore_version(
            db,
            1,
            7,
            5,
            expected_update_time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=8))),
        )

        self.assertEqual(restored.resume_data["basics"]["name"], "旧版")

    def test_version_of_another_resume_is_404(self):
        db = _VersionDb(_resume(), [_version(resume_id=99)])

        with self.assertRaises(AppException) as ctx:
            resume_service.get_version(db, 1, 7, 5)

        self.assertEqual(ctx.exception.code, 404)


def _make_client(db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resumes_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


def _summary_row(**overrides):
    payload = {
        "id": 5,
        "reason": "投递前存档",
        "source": "manual",
        "template_config": {"template_id": "classic"},
        "size_bytes": 2048,
        "create_time": datetime(2026, 1, 1, 11, 0, 0),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class VersionRoutesTest(unittest.TestCase):
    def test_list_returns_summaries_without_resume_data(self):
        rows = [_summary_row(), _summary_row(id=6, source="ai", reason="AI 修改前")]
        with patch("app.api.resumes.list_versions", return_value=(rows, 2)):
            resp = _make_client(object()).get("/api/resumes/7/versions")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["items"][0]["template_id"], "classic")
        self.assertNotIn("resume_data", data["items"][0], "列表带全量 JSON 会拖垮打开速度")
        self.assertEqual(data["items"][1]["source"], "ai")

    def test_other_users_version_is_404(self):
        with patch(
            "app.api.resumes.list_versions",
            side_effect=AppException("简历不存在", 404),
        ):
            resp = _make_client(object()).get("/api/resumes/99/versions")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], 404)

    def test_create_reports_whether_a_row_was_written(self):
        with patch("app.api.resumes.create_version", return_value=(SimpleNamespace(id=5), False)):
            resp = _make_client(object()).post("/api/resumes/7/versions", json={"reason": "存档"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], {"id": 5, "created": False})

    def test_detail_returns_the_full_snapshot_for_diffing(self):
        with patch("app.api.resumes.get_version", return_value=_version()):
            resp = _make_client(object()).get("/api/resumes/7/versions/5")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["resume_data"]["basics"]["name"], "旧版")
        self.assertEqual(data["template_id"], "classic")
        self.assertEqual(data["source"], "manual")

    def test_preview_uses_the_versions_own_template(self):
        resume = _resume()
        version = _version()
        with (
            patch("app.api.resumes.get_resume", return_value=resume),
            patch("app.api.resumes.get_version", return_value=version),
            patch("app.api.resumes.render_resume_html", return_value="<html>v</html>") as render,
        ):
            resp = _make_client(object()).get("/api/resumes/7/versions/5/preview-html")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "<html>v</html>")
        self.assertEqual(render.call_args.args[1], "classic", "预览必须用版本当时的模板")

    def test_restore_forwards_expected_update_time(self):
        resume = SimpleNamespace(
            id=7,
            user_id=1,
            title="张三的简历",
            language="zh-CN",
            resume_data={"basics": {"name": "旧版"}},
            template_id="classic",
            template_config={"template_id": "classic"},
            create_time=None,
            update_time=None,
        )
        with patch("app.api.resumes.restore_version", return_value=resume) as restore:
            resp = _make_client(object()).post(
                "/api/resumes/7/versions/5/restore",
                json={"expected_update_time": "2026-01-01T12:00:00"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["id"], 7)
        passed = restore.call_args.kwargs["expected_update_time"]
        self.assertEqual(passed, datetime(2026, 1, 1, 12, 0, 0))


if __name__ == "__main__":
    unittest.main()
