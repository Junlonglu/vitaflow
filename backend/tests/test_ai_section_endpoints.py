"""HTTP-level tests for the section tool-callback endpoints (ticket 04).

The AI service calls these when its get_resume_context / update_section tools fire.
Both must reject unauthenticated calls (service token), read the resume + history from
MySQL, and return either the requested section(s) or a validated pending payload — without
persisting anything. The DB is stubbed so this runs without MySQL/SQLite.
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


_RESUME_DATA = {
    "basics": {"name": "张三", "title": "后端工程师", "location": "广州", "phone": "13800138000"},
    "summary": {"content": "五年前端经验"},
    "work": [
        {"id": "work_1", "company": "A 公司", "position": "后端", "start_date": "2020-2021"},
        {"id": "work_2", "company": "B 公司", "position": "前端", "start_date": "2021-2022"},
    ],
}


def _make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    return TestClient(app)


def _patch_data(resume_data=_RESUME_DATA):
    resume = SimpleNamespace(resume_data=resume_data)
    history = [
        SimpleNamespace(role="user", content="帮我把 A 公司经历改成实习经历"),
        SimpleNamespace(role="assistant", content="好的，我准备了修改方案。"),
    ]
    return (
        patch.object(ai_module, "get_resume", return_value=resume),
        patch.object(ai_module, "list_chat_messages", return_value=history),
    )


class SectionToolEndpointTest(unittest.TestCase):
    def setUp(self):
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"

    def tearDown(self):
        settings.ai_service_token = self._original_token

    def _auth_headers(self):
        return service_headers(1, 2)

    def test_update_section_rejects_missing_token(self):
        client = _make_client()
        resp = client.post(
            "/api/ai/service/tool/update-section",
            json={"user_id": 1, "resume_id": 2, "section": "work", "proposed": [{"id": "work_1", "position": "实习"}]},
        )
        self.assertEqual(resp.status_code, 401)

    def test_update_section_valid_change_returns_pending_payload(self):
        patched = _patch_data()
        with patched[0], patched[1]:
            client = _make_client()
            resp = client.post(
                "/api/ai/service/tool/update-section",
                json={"user_id": 1, "resume_id": 2, "section": "work", "proposed": [{"id": "work_1", "position": "实习"}]},
                headers=self._auth_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["data"]["ok"])
        work = body["data"]["optimized_resume_data"]["work"]
        self.assertIn("实习", work[0]["position"])
        self.assertEqual(work[1]["company"], "B 公司")  # sibling untouched
        self.assertTrue(any("实习" in item for item in body["data"]["suggestions"]))

    def test_update_section_hallucinated_number_is_blocked(self):
        patched = _patch_data()
        with patched[0], patched[1]:
            client = _make_client()
            resp = client.post(
                "/api/ai/service/tool/update-section",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "section": "work",
                    "proposed": [{"id": "work_1", "start_date": "2030-2031"}],
                },
                headers=self._auth_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["data"]["ok"])
        self.assertIn("量化", body["data"]["reason"])

    def test_update_section_explicit_removal(self):
        patched = _patch_data()
        with patched[0], patched[1]:
            client = _make_client()
            resp = client.post(
                "/api/ai/service/tool/update-section",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "section": "work",
                    "proposed": [],
                    "removed_ids": ["work_1"],
                },
                headers=self._auth_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["data"]["ok"])
        self.assertEqual([item["id"] for item in body["data"]["optimized_resume_data"]["work"]], ["work_2"])

    def test_get_resume_context_reads_requested_section(self):
        patched = _patch_data()
        with patched[0], patched[1]:
            client = _make_client()
            resp = client.get(
                "/api/ai/service/tool/get-resume-context",
                params={"user_id": 1, "resume_id": 2, "sections": "work"},
                headers=self._auth_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["data"]["ok"])
        self.assertEqual(body["data"]["sections"]["work"], _RESUME_DATA["work"])
        self.assertNotIn("basics", body["data"]["sections"])

    def test_get_resume_context_rejects_missing_token(self):
        client = _make_client()
        resp = client.get(
            "/api/ai/service/tool/get-resume-context",
            params={"user_id": 1, "resume_id": 2, "sections": "work"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_propose_change_applies_multiple_sections_atomically(self):
        patched = _patch_data()
        with patched[0], patched[1]:
            client = _make_client()
            resp = client.post(
                "/api/ai/service/tool/propose-change",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "updates": [
                        {"section": "work", "proposed": [{"id": "work_1", "position": "实习"}]},
                        {"section": "basics", "proposed": {"location": "深圳"}},
                    ],
                },
                headers=self._auth_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["data"]["ok"])
        data = body["data"]["optimized_resume_data"]
        self.assertEqual(data["work"][0]["position"], "实习")
        self.assertEqual(data["work"][1]["company"], "B 公司")
        self.assertEqual(data["basics"]["location"], "深圳")

    def test_propose_change_blocks_hallucinated_number(self):
        patched = _patch_data()
        with patched[0], patched[1]:
            client = _make_client()
            resp = client.post(
                "/api/ai/service/tool/propose-change",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "updates": [{"section": "work", "proposed": [{"id": "work_1", "start_date": "2030-2032"}]}],
                },
                headers=self._auth_headers(),
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["data"]["ok"])
        self.assertIn("量化", body["data"]["reason"])


if __name__ == "__main__":
    unittest.main()
