"""HTTP-level tests for the update_basics_field tool callback endpoint.

The AI service calls this endpoint when its update_basics_field tool fires. It
must reject unauthenticated calls (service token), load the resume + history from
MySQL, run the numeric anti-hallucination guard, and return the pending payload —
without persisting anything (stream_ai_service_chat writes the row).

The DB is stubbed so this runs without MySQL/SQLite (the local venv cannot load
_sqlite3). get_db is overridden and get_resume/list_chat_messages are patched.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.service_ticket_support import service_headers
from app.api import ai as ai_module
from app.api.ai import router as ai_router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers

_CURRENT_RESUME_DATA = {
    "basics": {
        "name": "张三",
        "title": "后端工程师",
        "location": "广州",
        "phone": "13800138000",
    }
}


def _make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    return TestClient(app)


class UpdateBasicsFieldEndpointTest(unittest.TestCase):
    def _patch_data(self):
        resume = SimpleNamespace(resume_data=_CURRENT_RESUME_DATA)
        history = [
            SimpleNamespace(role="user", content="帮我把地点从广州改成深圳"),
            SimpleNamespace(role="assistant", content="好的，我准备了修改方案。"),
        ]
        return (
            patch.object(ai_module, "get_resume", return_value=resume),
            patch.object(ai_module, "list_chat_messages", return_value=history),
        )

    def setUp(self):
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"

    def tearDown(self):
        settings.ai_service_token = self._original_token

    def test_rejects_request_without_service_token(self):
        client = _make_client()
        resp = client.post(
            "/api/ai/service/tool/update-basics-field",
            json={"user_id": 1, "resume_id": 2, "basics": {"location": "深圳"}},
        )
        self.assertEqual(resp.status_code, 401)

    def test_rejects_tampered_supplied_token(self):
        client = _make_client()
        resp = client.post(
            "/api/ai/service/tool/update-basics-field",
            json={"user_id": 1, "resume_id": 2, "basics": {"location": "深圳"}},
            headers={"X-AI-Service-Token": "wrong"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_valid_basics_change_returns_pending_payload(self):
        with self._patch_data()[0], self._patch_data()[1]:
            client = _make_client()
            resp = client.post(
                "/api/ai/service/tool/update-basics-field",
                json={"user_id": 1, "resume_id": 2, "basics": {"location": "深圳"}},
                headers=service_headers(1, 2),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["data"]["ok"])
        self.assertEqual(body["data"]["optimized_resume_data"]["basics"]["location"], "深圳")
        self.assertEqual(body["data"]["optimized_resume_data"]["basics"]["name"], "张三")
        self.assertTrue(any("所在城市" in item for item in body["data"]["suggestions"]))

    def test_hallucinated_number_is_blocked(self):
        # A phone number never present in the resume/conversation is blocked.
        with self._patch_data()[0], self._patch_data()[1]:
            client = _make_client()
            resp = client.post(
                "/api/ai/service/tool/update-basics-field",
                json={"user_id": 1, "resume_id": 2, "basics": {"phone": "13999999999"}},
                headers=service_headers(1, 2),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["data"]["ok"])
        self.assertIn("量化", body["data"]["reason"])
        # 统一错误信封：反幻觉拦截属于 business——模型原样重试没有意义。
        self.assertEqual(body["data"]["category"], "business")
        self.assertFalse(body["data"]["retryable"])


if __name__ == "__main__":
    unittest.main()
