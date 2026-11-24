"""联网搜索证据（web_search → 反幻觉守卫）契约。

ai-service 的 web_search 工具把检索结果 POST 到 /service/tool/search-evidence，
以 (user_id, resume_id) 为键存入内存 TTL 存储；数字反幻觉守卫把这些证据行
作为第四证据源——搜索到的行业/JD 类数字可以写入提案，无证据的仍被拦截。
"""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.service_ticket_support import service_headers
from app.api import ai as ai_module
from app.api.ai import router as ai_router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.services import search_evidence_service
from app.services.search_evidence_service import (
    clear_search_evidence,
    get_recent_search_evidence,
    store_search_evidence,
)
from app.services.ai_chat_service import validate_section_update


class SearchEvidenceStoreTest(unittest.TestCase):
    def setUp(self):
        clear_search_evidence(1, 2)

    def tearDown(self):
        clear_search_evidence(1, 2)

    def test_store_get_roundtrip_and_key_isolation(self):
        stored = store_search_evidence(1, 2, "前端 薪资", [{"title": "行业报告", "url": "https://a", "snippet": "覆盖用户量 300 万"}])
        self.assertEqual(stored, 1)
        lines = get_recent_search_evidence(1, 2)
        self.assertEqual(len(lines), 1)
        self.assertIn("300 万", lines[0])
        self.assertIn("行业报告", lines[0])
        self.assertEqual(get_recent_search_evidence(1, 3), [])

    def test_ttl_expiry(self):
        store_search_evidence(1, 2, "q", [{"title": "T", "url": "u", "snippet": "S"}])
        future = search_evidence_service.time.time() + 31 * 60
        with patch.object(search_evidence_service.time, "time", return_value=future):
            self.assertEqual(get_recent_search_evidence(1, 2), [])

    def test_entry_cap_trims_oldest(self):
        # 12 次搜索 × 5 条 = 60 条 → 只保留最近 50 条
        for i in range(12):
            store_search_evidence(1, 2, f"q{i}", [{"title": f"T{i}", "url": "u", "snippet": f"S{j}"} for j in range(5)])
        lines = get_recent_search_evidence(1, 2)
        self.assertEqual(len(lines), search_evidence_service._MAX_ENTRIES)
        self.assertIn("T11", lines[-1])

    def test_invalid_items_are_skipped(self):
        stored = store_search_evidence(1, 2, "q", ["junk", {"title": "", "url": "", "snippet": ""}, {"title": "T", "url": "", "snippet": "S"}])
        self.assertEqual(stored, 1)


class SearchEvidenceGuardTest(unittest.TestCase):
    _CURRENT = {"work": [{"id": "w1", "company": "某科技", "description": "<p>负责订单服务</p>"}]}

    def tearDown(self):
        clear_search_evidence(7, 3)

    def test_claim_from_search_evidence_passes_guard(self):
        result = validate_section_update(
            self._CURRENT,
            "work",
            [{"id": "w1", "description": "<p>负责服务日均订单 300 万，行业领先</p>"}],
            search_evidence=["前端 薪资｜行业报告｜该公司日均订单 300 万"],
        )
        merged, suggestions, issue = result
        self.assertFalse(issue)
        self.assertIn("300 万", merged["work"][0]["description"])

    def test_unsupported_claim_still_blocked(self):
        merged, suggestions, issue = validate_section_update(
            self._CURRENT,
            "work",
            [{"id": "w1", "description": "<p>负责服务日均订单 999 万</p>"}],
        )
        self.assertIsNone(merged)
        self.assertIn("量化", issue)


def _make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app)


class SearchEvidenceEndpointTest(unittest.TestCase):
    def setUp(self):
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"
        clear_search_evidence(1, 2)

    def tearDown(self):
        settings.ai_service_token = self._original_token
        clear_search_evidence(1, 2)

    def test_rejects_request_without_service_token(self):
        resp = _make_client().post(
            "/api/ai/service/tool/search-evidence",
            json={"user_id": 1, "resume_id": 2, "query": "q", "results": []},
        )
        self.assertEqual(resp.status_code, 401)

    def test_stores_evidence_with_valid_token(self):
        resp = _make_client().post(
            "/api/ai/service/tool/search-evidence",
            json={
                "user_id": 1,
                "resume_id": 2,
                "query": "前端 薪资",
                "results": [{"title": "行业报告", "url": "https://a", "snippet": "S"}],
            },
            headers=service_headers(1, 2),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["ok"])
        self.assertEqual(resp.json()["data"]["stored"], 1)
        self.assertEqual(len(get_recent_search_evidence(1, 2)), 1)


if __name__ == "__main__":
    unittest.main()
