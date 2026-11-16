"""用户侧模板接口与内置模板同步。

模板列表/详情是匿名可读的；两个端点都把 db 传进 service，因此一次 GET 会顺带
把代码内置模板 upsert 进库（_sync_builtin_templates）。这里固定三件事：接口
匿名可读且响应包装正确、不存在的模板收敛成 404、GET 确实会写库并有幂等保护。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.templates import router as templates_router
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.services import template_service


def _make_client(db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(templates_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class TemplateRouteTest(unittest.TestCase):
    def test_list_is_anonymous_and_wrapped(self):
        items = [{"template_id": "tech", "name": "技术岗位", "preview_html": "<div/>"}]
        with patch("app.api.templates.list_templates", return_value=items) as mock_list:
            # 不 override get_current_user：匿名访问也必须能拿到列表。
            resp = _make_client(MagicMock()).get("/api/templates")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"], items)
        mock_list.assert_called_once()

    def test_detail_returns_one_template(self):
        item = {"template_id": "tech", "name": "技术岗位"}
        with patch("app.api.templates.get_template", return_value=item) as mock_get:
            resp = _make_client(MagicMock()).get("/api/templates/tech")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["template_id"], "tech")
        mock_get.assert_called_once()

    def test_missing_template_is_404(self):
        with patch("app.api.templates.get_template", return_value=None):
            resp = _make_client(MagicMock()).get("/api/templates/nope")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], 404)
        self.assertIn("不存在", resp.json()["message"])


class _SyncDb:
    """只实现 _sync_builtin_templates 用到的方法，记录 add / commit。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0

    def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: list(self.rows))

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def commit(self):
        self.commits += 1


class BuiltinTemplateSyncTest(unittest.TestCase):
    def test_empty_db_seeds_all_builtin_templates(self):
        db = _SyncDb()
        with patch.object(template_service, "render_resume_html", return_value="<div/>"):
            rows = template_service.ordered_template_records(db)
        # 12 个内置模板全部入库，且只提交一次。
        self.assertEqual(len(template_service.TEMPLATES), 12)
        self.assertEqual(len(db.added), 12)
        self.assertEqual(db.commits, 1)
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["sort_order"], 1)

    def test_second_sync_is_idempotent(self):
        db = _SyncDb()
        with patch.object(template_service, "render_resume_html", return_value="<div/>"):
            template_service.ordered_template_records(db)
            first_commits = db.commits
            template_service.ordered_template_records(db)
        # 已存在且字段一致时不再 add、不再 commit。
        self.assertEqual(len(db.added), 12)
        self.assertEqual(db.commits, first_commits)

    def test_hidden_templates_are_filtered_out(self):
        db = _SyncDb()
        with patch.object(template_service, "render_resume_html", return_value="<div/>"):
            rows = template_service.ordered_template_records(db, include_hidden=False)
        self.assertEqual(len(rows), 12)
        for row in rows:
            self.assertTrue(row["is_visible"])


if __name__ == "__main__":
    unittest.main()
