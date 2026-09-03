"""Tests for the /api/ai/service/usage billing endpoint (ticket 07).

The Node AI service aggregates per-model token usage from the pi event stream
and posts it here once a chat turn ends. Each entry must be settled at its own
model granularity: the config mapped by ``pi_model_ref`` supplies the chat
rates (billing_override), and settlement runs through the SAME task +
settle_task_flow_points ledger path the legacy LangChain chat uses, so Flow
Points balances and transactions reconcile (对账通过).

The DB is stubbed (no SQLite/MySQL, same pattern as test_ai_basics_endpoint);
create_ai_task / finish_ai_task / the pi_model_ref lookup are patched and their
arguments captured.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ai as ai_module
from app.api.ai import router as ai_router
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AppException, register_exception_handlers

_PI_REF = "deepseek/deepseek-v4-flash-vision-exp"

_USAGE_PAYLOAD = {
    "user_id": 7,
    "resume_id": 3,
    "usage": [
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash-vision-exp",
            "requests": 2,
            "input_tokens": 160,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_write_tokens": 16,
            "total_tokens": 236,
        }
    ],
}


def _make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: _DbStub()
    return TestClient(app)


class _DbStub(SimpleNamespace):
    """Minimal session stand-in: only db.get (user lookup) is touched here —
    the task/settle helpers are patched, so nothing else hits the DB."""

    def get(self, _model, _pk):
        return SimpleNamespace(id=7, role="user", flow_points=0)


def _stub_task():
    return SimpleNamespace(id=101, points_used=0, tokens_used=0, input_data={})


class AiServiceUsageEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_client()
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"

    def tearDown(self):
        settings.ai_service_token = self._original_token

    def _post(self, token: str | None = "test-token", payload: dict | None = None):
        headers = {"x-ai-service-token": token} if token else {}
        return self.client.post("/api/ai/service/usage", json=payload or _USAGE_PAYLOAD, headers=headers)

    def test_rejects_unauthenticated_calls(self):
        resp = self._post(token="wrong-token")
        self.assertEqual(resp.status_code, 401)

    def test_bills_mapped_model_with_its_chat_rates(self):
        config = SimpleNamespace(
            id=5,
            name="DeepSeek V4 Flash Vision",
            supports_multimodal=True,
            pi_model_ref=_PI_REF,
            chat_billing_override=lambda: {
                "points_per_call": 8.0,
                "points_per_million_input_tokens": 2000.0,
                "points_per_million_output_tokens": 8000.0,
            },
        )
        task = _stub_task()
        created: dict = {}

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None):
            created.update(
                user_id=user_id,
                task_type=task_type,
                resume_id=resume_id,
                input_data=input_data,
                model_name=model_name,
            )
            return task

        finished: dict = {}

        def fake_finish_ai_task(db, task_arg, status, error_message=None, output_data=None, token_usage=None):
            finished.update(status=status, output_data=output_data, token_usage=token_usage)
            task_arg.points_used = 1.72
            task_arg.tokens_used = 210

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=config) as lookup,
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=fake_finish_ai_task),
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 200)
        lookup.assert_called_once()
        self.assertEqual(lookup.call_args[0][1], _PI_REF)

        # One ai_chat task per usage entry, keyed to the mapped model.
        self.assertEqual(created["user_id"], 7)
        self.assertEqual(created["task_type"], "ai_chat")
        self.assertEqual(created["resume_id"], 3)
        self.assertEqual(created["model_name"], "DeepSeek V4 Flash Vision")
        self.assertEqual(created["input_data"]["pi_model_ref"], _PI_REF)
        # The per-model rates ride into the task so settle_task_flow_points
        # computes the exact legacy amount.
        self.assertEqual(
            created["input_data"]["billing_override"],
            {"points_per_call": 8.0, "points_per_million_input_tokens": 2000.0, "points_per_million_output_tokens": 8000.0},
        )
        # Actual (non-estimated) usage is recorded on the task.
        self.assertEqual(created["input_data"]["token_usage"]["input_tokens"], 160)
        self.assertEqual(created["input_data"]["token_usage"]["output_tokens"], 50)
        self.assertFalse(created["input_data"]["token_usage"]["estimated"])

        self.assertEqual(finished["status"], "success")
        self.assertEqual(finished["token_usage"]["total_tokens"], 210)

        body = resp.json()
        entry = body["data"]["entries"][0]
        self.assertEqual(entry["pi_model_ref"], _PI_REF)
        self.assertTrue(entry["mapped"])
        self.assertEqual(entry["model_config_id"], 5)
        self.assertEqual(entry["points_used"], 1.72)
        self.assertEqual(entry["tokens_used"], 210)

    def test_bills_unmapped_ref_at_default_rule(self):
        task = _stub_task()
        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=lambda db, user_id, task_type, **kwargs: task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: setattr(t, "points_used", 0.5)),
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        entry = body["data"]["entries"][0]
        # An unmapped ref must never lose the charge: it falls back to the
        # default ai_chat rule, keyed by the raw ref.
        self.assertFalse(entry["mapped"])
        self.assertIsNone(entry["model_config_id"])
        self.assertEqual(entry["points_used"], 0.5)

    def test_settlement_failure_marks_task_failed_not_pending(self):
        # 结算抛 402（余额不足以覆盖实际 token 结算）时，任务必须落成 failed
        # 走旧链路的退款口径，而不是永远悬挂在 pending、charge 丢失。
        task = _stub_task()
        finished: list[str] = []

        def fake_finish(db, task_arg, status, error_message=None, **_kw):
            finished.append(status)
            if status == "success":
                raise AppException("Flow Points 不足：本次至少需要 3.20 点", 402)
            task_arg.points_used = 0

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=lambda db, user_id, task_type, **kwargs: task),
            patch.object(ai_module, "finish_ai_task", side_effect=fake_finish),
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(finished, ["success", "failed"])
        entry = resp.json()["data"]["entries"][0]
        self.assertFalse(entry["billed"])
        self.assertIn("Flow Points 不足", entry["error"])

    def test_unknown_user_is_rejected(self):
        def fake_get(_model, _pk):
            return None

        app_db = SimpleNamespace(get=fake_get)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(ai_router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: app_db

        with patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None):
            resp = TestClient(app).post(
                "/api/ai/service/usage", json=_USAGE_PAYLOAD, headers={"x-ai-service-token": "test-token"}
            )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
