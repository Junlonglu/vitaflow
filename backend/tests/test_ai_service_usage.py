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
    "feature": "polish",
    "tool_calls": 2,
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

    def add(self, _entity):
        return None

    def commit(self):
        return None


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

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(
                user_id=user_id,
                task_type=task_type,
                resume_id=resume_id,
                input_data=input_data,
                model_name=model_name,
                feature=feature,
            )
            return task

        finished: dict = {}

        def fake_finish_ai_task(db, task_arg, status, error_message=None, output_data=None, token_usage=None, settle=True):
            finished.update(status=status, output_data=output_data, token_usage=token_usage, settle=settle)
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
        self.assertEqual(created["feature"], "polish")
        self.assertEqual(created["input_data"]["pi_model_ref"], _PI_REF)
        self.assertEqual(created["input_data"]["tool_calls"], 2)
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

    def test_failed_turn_records_failed_task_without_billing(self):
        # 失败回合回报（usage 为空 + status=failed）：只落一条 failed 任务行让成功率
        # 反映真实失败，不做任何结算/扣费；未知 feature 丢弃而不是报错。
        created: dict = {}
        finished: dict = {}

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(user_id=user_id, task_type=task_type, resume_id=resume_id, feature=feature)
            return _stub_task()

        def fake_finish_ai_task(db, task_arg, status, error_message=None, output_data=None, token_usage=None):
            finished.update(status=status, error_message=error_message, token_usage=token_usage)

        with (
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=fake_finish_ai_task),
        ):
            resp = self._post(
                payload={
                    "user_id": 7,
                    "resume_id": 3,
                    "feature": "no_such_feature",
                    "status": "failed",
                    "error_message": "模型超时",
                    "usage": [],
                }
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(created["task_type"], "ai_chat")
        self.assertIsNone(created["feature"], "unknown feature values are dropped")
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error_message"], "模型超时")
        self.assertIsNone(finished["token_usage"], "failed turns are never billed")
        self.assertEqual(resp.json()["data"]["entries"], [])

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


_TOOL_QUALITY_PAYLOAD = {
    **_USAGE_PAYLOAD,
    "tool_quality": [
        {"name": "get_resume_context", "status": "end", "ok": True, "duration_ms": 45},
        {"name": "update_section", "status": "end", "ok": False, "category": "validation", "duration_ms": 120},
    ],
}


class ToolQualityPersistenceTest(unittest.TestCase):
    """工具质量摘要落库（事实记录与消息表解耦——删简历后看板口径仍一致）。

    摘要只随第一个任务行落库（回合级数据，多模型回合不得在聚合时翻倍）；
    内容型字段（label/detail）即使混进请求也要被白名单剥掉。
    """

    def setUp(self):
        self.client = _make_client()
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"

    def tearDown(self):
        settings.ai_service_token = self._original_token

    def test_quality_rides_on_first_task_row_only(self):
        created: list[dict] = []
        multi_model = {
            **_TOOL_QUALITY_PAYLOAD,
            "usage": [_USAGE_PAYLOAD["usage"][0], {**_USAGE_PAYLOAD["usage"][0], "model": "other-model"}],
        }

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.append({"input_data": input_data, "model_name": model_name})
            return _stub_task()

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: None),
        ):
            resp = self.client.post("/api/ai/service/usage", json=multi_model, headers={"x-ai-service-token": "test-token"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(created), 2)
        self.assertEqual(len(created[0]["input_data"]["tool_quality"]), 2)
        self.assertNotIn("tool_quality", created[1]["input_data"])

    def test_extra_content_fields_are_stripped(self):
        # 内容型字段必须死在消息表上：即使上游误传 label/detail 也不得落库
        payload = {
            **_TOOL_QUALITY_PAYLOAD,
            "tool_quality": [
                {"name": "update_section", "status": "end", "ok": True, "label": "负责订单服务…", "detail": "简历原文片段", "id": "tc1", "duration_ms": 30},
            ],
        }
        created: dict = {}

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(input_data=input_data)
            return _stub_task()

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: None),
        ):
            resp = self.client.post("/api/ai/service/usage", json=payload, headers={"x-ai-service-token": "test-token"})

        self.assertEqual(resp.status_code, 200)
        step = created["input_data"]["tool_quality"][0]
        self.assertEqual(step, {"name": "update_section", "status": "end", "ok": True, "duration_ms": 30})

    def test_failed_turn_stores_quality_on_failed_task_row(self):
        created: dict = {}

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(input_data=input_data)
            return _stub_task()

        with (
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: None),
        ):
            resp = self.client.post(
                "/api/ai/service/usage",
                json={
                    "user_id": 7,
                    "resume_id": 3,
                    "feature": "chat",
                    "status": "failed",
                    "error_message": "模型超时",
                    "usage": [],
                    "tool_quality": [{"name": "web_search", "status": "end", "ok": True, "duration_ms": 800}],
                },
                headers={"x-ai-service-token": "test-token"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(created["input_data"]["tool_quality"], [{"name": "web_search", "status": "end", "ok": True, "duration_ms": 800}])

    def test_more_than_50_steps_are_truncated_not_rejected(self):
        # 质量摘要是辅助数据：超限截断即可，绝不能 422 拒掉整份计费上报
        payload = {
            **_TOOL_QUALITY_PAYLOAD,
            "tool_quality": [{"name": "web_search", "status": "end", "ok": True}] * 51,
        }
        created: dict = {}

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(input_data=input_data)
            return _stub_task()

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: None),
        ):
            resp = self.client.post("/api/ai/service/usage", json=payload, headers={"x-ai-service-token": "test-token"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(created["input_data"]["tool_quality"]), 50)

    def test_turn_timing_rides_on_first_task_row_only(self):
        multi_model = {
            **_USAGE_PAYLOAD,
            "turn_timing": {"first_token_ms": 1240, "tool_ms": 45, "total_ms": 8300, "loader_ms": 6, "session_create_ms": 4, "provider_ttfb_ms": 1180},
            "usage": [_USAGE_PAYLOAD["usage"][0], {**_USAGE_PAYLOAD["usage"][0], "model": "other-model"}],
        }
        created: list[dict] = []

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.append({"input_data": input_data, "model_name": model_name})
            return _stub_task()

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: None),
        ):
            resp = self.client.post("/api/ai/service/usage", json=multi_model, headers={"x-ai-service-token": "test-token"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(created), 2)
        self.assertEqual(created[0]["input_data"]["turn_timing"]["total_ms"], 8300)
        self.assertNotIn("turn_timing", created[1]["input_data"])

    def test_failed_turn_stores_turn_timing(self):
        timing = {"first_token_ms": 1240, "tool_ms": 45, "total_ms": 8300, "loader_ms": 6, "session_create_ms": 4, "provider_ttfb_ms": 1180}
        created: dict = {}

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(input_data=input_data)
            return _stub_task()

        with (
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: None),
        ):
            resp = self.client.post(
                "/api/ai/service/usage",
                json={
                    "user_id": 7,
                    "resume_id": 3,
                    "feature": "chat",
                    "status": "failed",
                    "error_message": "模型超时",
                    "usage": [],
                    "tool_calls": 1,
                    "turn_timing": timing,
                },
                headers={"x-ai-service-token": "test-token"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(created["input_data"]["turn_timing"], timing)
        self.assertEqual(created["input_data"]["tool_calls"], 1, "失败回合的工具调用数也要落库")

    def test_malformed_timing_values_do_not_reject_report(self):
        # 回合耗时是辅助数据：负数/浮点不得把整份计费上报顶成 422（聚合侧过滤）
        payload = {
            **_USAGE_PAYLOAD,
            "turn_timing": {"total_ms": -5, "first_token_ms": 1.5},
        }
        created: dict = {}

        def fake_create_ai_task(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(input_data=input_data)
            return _stub_task()

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=fake_create_ai_task),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: None),
        ):
            resp = self.client.post("/api/ai/service/usage", json=payload, headers={"x-ai-service-token": "test-token"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(created["input_data"]["turn_timing"], {"total_ms": -5, "first_token_ms": 1.5})


class ByokUsageBillingTest(unittest.TestCase):
    """用户自带模型：仍落一条 AI 记录，但不结算平台 Flow Points。"""

    def setUp(self):
        self.client = _make_client()
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"

    def tearDown(self):
        settings.ai_service_token = self._original_token

    def test_byok_turn_is_recorded_without_settlement(self):
        payload = {
            **_USAGE_PAYLOAD,
            "usage": [{**_USAGE_PAYLOAD["usage"][0], "provider": "byok-42-a1b2c3", "model": "my-model"}],
        }
        task = _stub_task()
        created: dict = {}
        finished: dict = {}

        def fake_create(db, user_id, task_type, resume_id=None, input_data=None, model_name=None, feature=None):
            created.update(input_data=input_data, model_name=model_name)
            return task

        def fake_finish(db, task_arg, status, **kwargs):
            finished.update(status=status, settle=kwargs.get("settle", True))
            task_arg.points_used = 0.9  # 模拟结算写入，byok 分支必须覆盖回 0

        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=fake_create),
            patch.object(ai_module, "finish_ai_task", side_effect=fake_finish),
        ):
            resp = self.client.post("/api/ai/service/usage", json=payload, headers={"x-ai-service-token": "test-token"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(finished["settle"], False)
        self.assertEqual(task.points_used, 0)
        self.assertEqual(created["input_data"]["billing_mode"], "byok")
        entry = resp.json()["data"]["entries"][0]
        self.assertFalse(entry["billed"])
        self.assertEqual(entry["billing_mode"], "byok")

    def test_platform_turn_still_settles(self):
        finished: dict = {}
        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=lambda db, user_id, task_type, **kwargs: _stub_task()),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, t, status, **kw: finished.update(settle=kw.get("settle", True))),
        ):
            self.client.post("/api/ai/service/usage", json=_USAGE_PAYLOAD, headers={"x-ai-service-token": "test-token"})
        self.assertEqual(finished["settle"], True)


if __name__ == "__main__":
    unittest.main()
