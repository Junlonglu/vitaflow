"""Tests for /api/ai/service/trace — the L1 agent trajectory sink (P0).

轨迹是分析资产而不是业务数据：它必须在删简历、注销用户之后仍然可用于
"改了提示词之后效果变好没有"的对比，所以这里只校验三件事——鉴权、幂等
（重试打回来不能产生两行）、长度上限（异常事件流不能把行撑爆）。
"""
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai import router as ai_router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.models.ai_trace import AiAgentTrace

_TRACE_PAYLOAD = {
    "trace_id": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    "turn_id": 123,
    "session_id": 9,
    "user_id": 7,
    "resume_id": 3,
    "outcome": "ok",
    "model": "deepseek/deepseek-v4-flash",
    "prompt_versions": {"rules": "r7f3a2c91d0e", "schema": "s1b2c3d4e5f6", "skill": None, "prompt_chars": 6377, "agent": "ai-service@0.1.0"},
    "events": [
        {"v": 1, "seq": 0, "type": "turn_start", "t_ms": 0, "trace_id": "0f1e"},
        {"v": 1, "seq": 1, "type": "tool_call", "t_ms": 820, "name": "update_resume", "args_digest": {"sections": ["basics"], "items": 0, "fields": 1}},
        {"v": 1, "seq": 2, "type": "tool_result", "t_ms": 960, "name": "update_resume", "ok": True, "duration_ms": 140},
        {"v": 1, "seq": 3, "type": "turn_end", "t_ms": 4200, "outcome": "ok", "reply_chars": 412},
    ],
}


class _DbStub:
    """Only the two calls the endpoint makes: one lookup, add, commit."""

    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.commits = 0

    def scalar(self, _query):
        return self.existing

    def add(self, entity):
        self.added.append(entity)

    def commit(self):
        self.commits += 1


def _make_client(db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class AgentTraceEndpointTest(unittest.TestCase):
    def setUp(self):
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"

    def tearDown(self):
        settings.ai_service_token = self._original_token

    def _post(self, db, payload=None):
        client = _make_client(db)
        return client.post("/api/ai/service/trace", json=payload or _TRACE_PAYLOAD, headers={"x-ai-service-token": "test-token"})

    def test_persists_one_row_per_turn(self):
        db = _DbStub()
        resp = self._post(db)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["event_count"], 4)
        self.assertEqual(len(db.added), 1)
        row = db.added[0]
        self.assertIsInstance(row, AiAgentTrace)
        self.assertEqual(row.trace_id, _TRACE_PAYLOAD["trace_id"])
        self.assertEqual(row.turn_id, 123)
        self.assertEqual(row.session_id, 9)
        self.assertEqual(row.outcome, "ok")
        self.assertEqual(row.model, "deepseek/deepseek-v4-flash")
        self.assertEqual(row.event_count, 4)
        # 分段版本必须落盘：没有它就永久失去归因能力
        self.assertEqual(row.prompt_versions["rules"], "r7f3a2c91d0e")
        self.assertEqual(db.commits, 1)

    def test_retry_overwrites_the_same_row_instead_of_duplicating(self):
        existing = SimpleNamespace(trace_id=_TRACE_PAYLOAD["trace_id"], event_count=0)
        db = _DbStub(existing=existing)
        resp = self._post(db)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.added, [existing], "重试按 trace_id 覆盖同一行")
        self.assertEqual(existing.event_count, 4)
        self.assertEqual(existing.outcome, "ok")

    def test_rejects_a_call_without_the_service_token(self):
        db = _DbStub()
        client = _make_client(db)
        resp = client.post("/api/ai/service/trace", json=_TRACE_PAYLOAD)

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(db.added, [])

    def test_caps_event_count_so_a_runaway_stream_cannot_bloat_the_row(self):
        db = _DbStub()
        payload = dict(_TRACE_PAYLOAD, events=[{"v": 1, "seq": i, "type": "tool_call"} for i in range(300)])
        resp = self._post(db, payload)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.added[0].event_count, 200)
        self.assertEqual(resp.json()["data"]["event_count"], 200)


if __name__ == "__main__":
    unittest.main()
