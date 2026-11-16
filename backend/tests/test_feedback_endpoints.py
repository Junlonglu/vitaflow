"""用户反馈接口：提交（鉴权 + 净化 + 邮件通知）与我的反馈列表（隔离 + 分页）。

提交是「写库 + best-effort 邮件」：净化在入库前做，通知邮件失败只记 warning，
不能影响接口返回。列表严格按 user_id 隔离，分页参数有边界约束。
"""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.feedback import router as feedback_router
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.core.security import get_current_user


class _FeedbackDb:
    def __init__(self, items=None, total=0, notify_email=""):
        self.items = list(items or [])
        self.total = total
        self.notify_email = notify_email
        self.added = []
        self.commits = 0
        self.count_stmt = None
        self.list_stmt = None

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        # 真库 refresh 会回填自增 id / 时间，假库手动补上供响应序列化。
        if getattr(obj, "id", None) is None:
            obj.id = 101
        if getattr(obj, "create_time", None) is None:
            obj.create_time = datetime.now()

    def scalar(self, stmt):
        if "count" in str(stmt).lower():
            self.count_stmt = str(stmt)
            return self.total
        return None

    def scalars(self, stmt):
        self.list_stmt = str(stmt)
        return _ScalarResult(self.items)


class _ScalarResult(list):
    """既能被 list() 迭代（feedback 列表），也支持 .all()（templates 同步）。"""

    def all(self):
        return list(self)


def _make_client(db, with_user=True):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(feedback_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    if with_user:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    return TestClient(app)


def _feedback_item(**overrides):
    base = {
        "id": 1,
        "category": "bug",
        "content": "<p>导出失败</p>",
        "contact": "a@b.com",
        "status": "open",
        "admin_reply": None,
        "reply_time": None,
        "create_time": datetime.now(),
        "update_time": datetime.now(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FeedbackCreateTest(unittest.TestCase):
    def test_submit_requires_login(self):
        # 不 override get_current_user：受保护端点必须拒绝匿名请求。
        with patch("app.api.feedback.get_setting", return_value=""):
            resp = _make_client(_FeedbackDb(), with_user=False).post(
                "/api/feedback", json={"content": "快捷键无效"}
            )
        self.assertIn(resp.status_code, (401, 403))

    def test_content_is_sanitized_before_persist(self):
        db = _FeedbackDb()
        raw = '<p>你好</p><script>alert(1)</script><img src=x onerror="evil()">'
        cleaned = "<p>你好</p><img src=\"x\">"
        with (
            patch("app.api.feedback.sanitize_rich_text_html", return_value=cleaned) as sanitize,
            patch("app.api.feedback.get_setting", return_value=""),
        ):
            resp = _make_client(db).post("/api/feedback", json={"content": raw, "category": " bug "})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["message"], "反馈已提交")
        sanitize.assert_called_once_with(raw, allow_images=True)
        item = db.added[0]
        self.assertEqual(item.content, cleaned)  # 落库的是净化后的内容
        self.assertEqual(item.category, "bug")  # 分类去空白
        self.assertEqual(item.user_id, 7)
        self.assertEqual(item.status, "open")
        self.assertEqual(db.commits, 1)

    def test_empty_contact_becomes_none(self):
        db = _FeedbackDb()
        with patch("app.api.feedback.get_setting", return_value=""):
            resp = _make_client(db).post("/api/feedback", json={"content": "建议", "contact": "   "})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(db.added[0].contact)

    def test_no_notify_email_skips_mail(self):
        db = _FeedbackDb()
        with (
            patch("app.api.feedback.get_setting", return_value=""),
            patch("app.api.feedback.send_feedback_notification") as send,
        ):
            resp = _make_client(db).post("/api/feedback", json={"content": "建议"})
        self.assertEqual(resp.status_code, 200)
        send.assert_not_called()

    def test_mail_failure_is_swallowed(self):
        db = _FeedbackDb()
        with (
            patch("app.api.feedback.get_setting", return_value="admin@example.com"),
            patch("app.api.feedback.send_feedback_notification", side_effect=RuntimeError("SMTP 挂了")),
        ):
            resp = _make_client(db).post("/api/feedback", json={"content": "建议"})
        # 通知邮件是 best-effort：发信失败也要返回 200 并成功落库。
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(db.added), 1)

    def test_too_short_content_is_422(self):
        resp = _make_client(_FeedbackDb()).post("/api/feedback", json={"content": "1"})
        self.assertEqual(resp.status_code, 422)


class FeedbackListTest(unittest.TestCase):
    def test_list_is_scoped_to_current_user(self):
        db = _FeedbackDb(items=[_feedback_item()], total=1)
        with patch("app.api.feedback.sanitize_rich_text_html", side_effect=lambda v, **_: v):
            resp = _make_client(db).get("/api/feedback")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 10)
        # 查询条件里必须带 user_id = 7，避免看到别人的反馈。
        self.assertIn("user_feedbacks.user_id", db.count_stmt)
        self.assertIn("user_feedbacks.user_id", db.list_stmt)

    def test_page_size_above_limit_is_422(self):
        resp = _make_client(_FeedbackDb()).get("/api/feedback", params={"page_size": 51})
        self.assertEqual(resp.status_code, 422)

    def test_page_zero_is_422(self):
        resp = _make_client(_FeedbackDb()).get("/api/feedback", params={"page": 0})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
