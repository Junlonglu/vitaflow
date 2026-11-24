"""update_resume 服务间端点：Agent 的唯一写入入口。

四个旧写入工具（update_basics_field / update_section / propose_change /
import_info_fragments）在 Agent 侧合并成一个形状，这个端点就是那个形状的落点：
先把信息库条目并入快照，再按 id 合并模块改动，一次校验、一份待确认快照，
且不在这里落库。
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
        {"id": "work_1", "company": "A 公司", "position": "后端", "start_date": "2020.07"},
        {"id": "work_2", "company": "B 公司", "position": "前端", "start_date": "2021.07"},
    ],
}


def _make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    return TestClient(app)


def _patch(resume_data=None, imported=None):
    resume = SimpleNamespace(resume_data=resume_data or _RESUME_DATA)
    history = [
        SimpleNamespace(role="user", content="把 A 公司那条改成实习"),
        SimpleNamespace(role="assistant", content="好的，我准备了修改方案。"),
    ]
    stack = [
        patch.object(ai_module, "get_resume", return_value=resume),
        patch.object(ai_module, "list_chat_messages", return_value=history),
        patch.object(ai_module, "get_recent_search_evidence", return_value=[]),
    ]
    if imported is not None:
        stack.append(patch.object(ai_module, "import_info_fragments", return_value=imported))
    return stack


class UpdateResumeEndpointTest(unittest.TestCase):
    def setUp(self):
        self._original_token = settings.ai_service_token
        settings.ai_service_token = "test-token"

    def tearDown(self):
        settings.ai_service_token = self._original_token

    def _headers(self):
        return service_headers(1, 2)

    def test_rejects_missing_token(self):
        resp = _make_client().post(
            "/api/ai/service/tool/update-resume",
            json={"user_id": 1, "resume_id": 2, "updates": [{"section": "work", "proposed": []}]},
        )
        self.assertEqual(resp.status_code, 401)

    def test_merges_by_id_and_returns_pending_snapshot(self):
        stack = _patch()
        with stack[0], stack[1], stack[2]:
            resp = _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "session_id": 11,
                    "updates": [
                        {"section": "work", "proposed": [{"id": "work_1", "position": "实习生"}]},
                        {"section": "summary", "proposed": {"content": "三句话简介"}},
                    ],
                },
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertTrue(data["ok"])
        work = data["optimized_resume_data"]["work"]
        self.assertEqual(work[0]["position"], "实习生")
        self.assertEqual(work[1]["company"], "B 公司", "siblings stay untouched")
        self.assertEqual(data["optimized_resume_data"]["summary"]["content"], "三句话简介")
        self.assertTrue(data["suggestions"], "变更摘要不能为空")

    def test_basics_is_just_another_section(self):
        stack = _patch()
        with stack[0], stack[1], stack[2]:
            resp = _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "session_id": 11,
                    "updates": [{"section": "basics", "proposed": {"location": "深圳"}}],
                },
                headers=self._headers(),
            )
        data = resp.json()["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["optimized_resume_data"]["basics"]["location"], "深圳")
        self.assertEqual(data["optimized_resume_data"]["basics"]["name"], "张三", "field-level merge")

    def test_imports_from_library_then_applies_section_changes(self):
        imported_data = {
            **_RESUME_DATA,
            "projects": [{"id": "project_1", "name": "VitaFlow", "role": "全栈"}],
        }
        stack = _patch(imported=(imported_data, ["项目经历（VitaFlow）"], []))
        with stack[0], stack[1], stack[2], stack[3] as mock_import:
            resp = _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "session_id": 11,
                    "from_library": [3],
                    "updates": [{"section": "summary", "proposed": {"content": "新的简介"}}],
                },
                headers=self._headers(),
            )
        mock_import.assert_called_once()
        data = resp.json()["data"]
        self.assertTrue(data["ok"])
        # 导入的内容不会被后续模块合 coverage 掉：两者落在同一份快照上。
        self.assertEqual(data["optimized_resume_data"]["projects"][0]["name"], "VitaFlow")
        self.assertEqual(data["optimized_resume_data"]["summary"]["content"], "新的简介")
        self.assertTrue(any(label.startswith("导入信息库：") for label in data["suggestions"]))

    def test_empty_request_is_a_business_failure(self):
        stack = _patch()
        with stack[0], stack[1], stack[2]:
            resp = _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={"user_id": 1, "resume_id": 2, "session_id": 11},
                headers=self._headers(),
            )
        data = resp.json()["data"]
        self.assertFalse(data["ok"])
        self.assertEqual(data["category"], "business")

    def test_unknown_section_is_rejected(self):
        stack = _patch()
        with stack[0], stack[1], stack[2]:
            resp = _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "session_id": 11,
                    "updates": [{"section": "not_a_section", "proposed": {"x": 1}}],
                },
                headers=self._headers(),
            )
        data = resp.json()["data"]
        self.assertFalse(data["ok"])
        self.assertIn("不支持修改的简历模块", data["reason"])

    def test_missing_session_id_is_rejected_instead_of_falling_back(self):
        """RF-014：没有会话就不知道该用哪段历史，回落「最近活跃」会拿错上下文。"""
        stack = _patch()
        with stack[0], stack[1] as mock_messages, stack[2]:
            resp = _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "updates": [{"section": "summary", "proposed": {"content": "新的简介"}}],
                },
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("session_id", resp.json()["message"])
        mock_messages.assert_not_called()

    def test_history_is_scoped_to_the_turn_session(self):
        stack = _patch()
        with stack[0], stack[1] as mock_messages, stack[2]:
            _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "session_id": 42,
                    "updates": [{"section": "summary", "proposed": {"content": "新的简介"}}],
                },
                headers=self._headers(),
            )
        self.assertEqual(mock_messages.call_args.kwargs["session_id"], 42)

    def test_second_call_stacks_on_the_pending_snapshot(self):
        """RF-019：模型一轮里调两次，第二次要叠在第一次的结果上。"""
        pending = {
            **_RESUME_DATA,
            "work": [
                {"id": "work_1", "company": "A 公司", "position": "实习生", "start_date": "2020.07"},
                {"id": "work_2", "company": "B 公司", "position": "前端", "start_date": "2021.07"},
            ],
        }
        stack = _patch()
        with stack[0], stack[1], stack[2]:
            resp = _make_client().post(
                "/api/ai/service/tool/update-resume",
                json={
                    "user_id": 1,
                    "resume_id": 2,
                    "session_id": 11,
                    "base_resume_data": pending,
                    "updates": [{"section": "basics", "proposed": {"location": "深圳"}}],
                },
                headers=self._headers(),
            )
        data = resp.json()["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["optimized_resume_data"]["work"][0]["position"], "实习生", "第一次改动不能丢")
        self.assertEqual(data["optimized_resume_data"]["basics"]["location"], "深圳")
