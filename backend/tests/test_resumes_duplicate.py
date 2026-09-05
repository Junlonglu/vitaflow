"""HTTP-level tests for POST /resumes/{id}/duplicate.

The JD-optimize "save as new resume" flow calls it with an optional title; the
plain duplicate button calls it without a body. Both paths must copy the resume
under the requesting user's ownership. The service layer is stubbed — the route's
job is auth + parameter wiring.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.resumes import router as resumes_router
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.core.security import get_current_user
from app.schemas.resume import ResumeOut

from datetime import datetime


def _make_resume(**overrides):
    base = {
        "id": 7,
        "user_id": 1,
        "title": "张三的简历",
        "language": "zh-CN",
        "resume_data": {"basics": {"name": "张三"}},
        "template_id": "tech",
        "template_config": {"template_id": "tech"},
        "create_time": datetime.now(),
        "update_time": datetime.now(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_client():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resumes_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    return TestClient(app)


class DuplicateResumeEndpointTest(unittest.TestCase):
    def test_duplicate_without_body_defaults_title(self):
        created = _make_resume(id=8, title="张三的简历 副本")
        with patch("app.api.resumes.duplicate_resume", return_value=created) as mock_dup:
            resp = _make_client().post("/api/resumes/7/duplicate")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["title"], "张三的简历 副本")
        mock_dup.assert_called_once()
        self.assertEqual(mock_dup.call_args.args[1:3], (1, 7))
        self.assertIsNone(mock_dup.call_args.args[3])

    def test_duplicate_with_custom_title(self):
        created = _make_resume(id=9, title="张三的简历 · JD优化")
        with patch("app.api.resumes.duplicate_resume", return_value=created) as mock_dup:
            resp = _make_client().post("/api/resumes/7/duplicate", json={"title": "张三的简历 · JD优化"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["title"], "张三的简历 · JD优化")
        self.assertEqual(mock_dup.call_args.args[3], "张三的简历 · JD优化")

    def test_duplicate_response_is_resume_out_shape(self):
        created = _make_resume(id=10, title="任意")
        with patch("app.api.resumes.duplicate_resume", return_value=created):
            resp = _make_client().post("/api/resumes/7/duplicate", json={"title": "任意"})
        data = resp.json()["data"]
        # Must survive the strict ResumeOut validation (frontend contract).
        ResumeOut.model_validate(data)
        self.assertEqual(data["id"], 10)


if __name__ == "__main__":
    unittest.main()
