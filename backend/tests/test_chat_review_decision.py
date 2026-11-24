"""审阅模式（track-changes）的后端契约。

resolve_chat_change 支持 merged_resume_data（前端勾选变更后合并到当前简历数据），
preview-html 支持 ?message_id 渲染 pending 提案快照；条件不满足一律回退/报错，
绝不越权读取其他用户的提案。
"""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import resumes as resumes_module
from app.api.resumes import router as resumes_router
from app.core.database import get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.core.security import get_current_user
from app.models.ai_chat import AiChatMessage
from app.models.resume import ResumeVersion
from app.services.ai_chat_service import resolve_chat_change


def _message_stub(**overrides):
    base = dict(
        id=5,
        session_id=11,
        user_id=7,
        resume_id=3,
        role="assistant",
        content="已准备好修改方案",
        attachments=None,
        suggestions=[],
        optimized_resume_data={"basics": {"name": "快照名"}},
        tool_trace=None,
        timing=None,
        pending_questions=None,
        action_status="pending",
        create_time=datetime.now(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _DbStub:
    def __init__(self, message):
        self._message = message
        self.added = []
        self.resume = None

    def get(self, model, pk):
        return self._message if model is AiChatMessage else self.resume

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def flush(self):
        pass

    def scalars(self, _stmt):
        # 版本快照去重要查「最新一版」；stub 里没有历史版本。
        return SimpleNamespace(first=lambda: None)

    def scalar(self, _stmt):
        # 「有没有更新的 pending 提案」这类聚合查询：stub 里没有，返回 None。
        return None

    def execute(self, statement):
        """把 Core UPDATE 的 SET 落到对应的假对象上——真库里这一步由数据库完成。"""
        table = getattr(getattr(statement, "table", None), "name", "")
        target = self._message if table == "ai_chat_messages" else self.resume
        for raw_key, value in dict(getattr(statement, "_values", {})).items():
            # SQLAlchemy 2.0 的 _values 以 Column 对象为键，不是列名字符串。
            key = getattr(raw_key, "key", raw_key)
            # 值被包在 BindParameter 里；revision 是 `revision + 1` 表达式，没有 .value。
            value = getattr(value, "value", value)
            if key == "revision":
                setattr(target, key, int(getattr(target, "revision", 0) or 0) + 1)
            else:
                setattr(target, key, value)
        return SimpleNamespace(rowcount=1)

    def expire(self, obj, attrs=None):
        pass

    def rollback(self):
        pass


def _resume_stub(resume_data=None):
    return SimpleNamespace(
        id=3,
        user_id=7,
        language="zh-CN",
        resume_data=resume_data if resume_data is not None else {"basics": {"name": "旧名"}},
        template_config={},
        update_by=None,
        revision=0,
    )


class ResolveChatChangeMergedTest(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=7)

    def _run(self, message, resume, merged=None, action="apply", expect_revision=None):
        db = _DbStub(message)
        db.resume = resume
        out, resume_data = resolve_chat_change(
            db, self.user, resume, message.id, action, merged, expect_revision=expect_revision
        )
        return db, out, resume_data

    def test_apply_with_merged_persists_merged_data(self):
        message = _message_stub()
        resume = _resume_stub()
        db, out, resume_data = self._run(message, resume, merged={"basics": {"name": "合并名"}})
        self.assertEqual(out.action_status, "applied")
        self.assertEqual(resume.resume_data["basics"]["name"], "合并名")
        self.assertEqual(resume_data["basics"]["name"], "合并名")
        versions = [item for item in db.added if isinstance(item, ResumeVersion)]
        self.assertEqual(len(versions), 1)

    def test_apply_without_merged_falls_back_to_snapshot(self):
        message = _message_stub()
        resume = _resume_stub()
        db, out, resume_data = self._run(message, resume)
        self.assertEqual(out.action_status, "applied")
        self.assertEqual(resume.resume_data["basics"]["name"], "快照名")

    def test_apply_with_invalid_merged_raises(self):
        message = _message_stub()
        resume = _resume_stub()
        with self.assertRaises(AppException):
            self._run(message, resume, merged={})

    def test_merged_translation_titles_survive_normalization(self):
        # 翻译提案：简历 language 仍是 zh-CN，但 merged 的标题已是英文默认名——
        # 规范化不得把已知默认标题重置回中文（吞掉标题翻译变更）
        merged = {
            "basics": {"name": "Zhang San"},
            "summary": {"content": "Three years of backend experience."},
            "layout": {"section_order": ["basics", "summary"], "hidden_sections": [], "section_titles": {"basics": "Basic Information", "summary": "Summary"}},
        }
        message = _message_stub(optimized_resume_data=merged)
        resume = _resume_stub({"basics": {"name": "张三"}, "summary": {"content": "三年后端经验。"}, "layout": {"section_order": ["basics", "summary"], "hidden_sections": [], "section_titles": {"basics": "基本信息", "summary": "个人简介"}}})
        db, out, resume_data = self._run(message, resume, merged=merged)
        self.assertEqual(out.action_status, "applied")
        self.assertEqual(resume.resume_data["layout"]["section_titles"]["basics"], "Basic Information")
        self.assertEqual(resume.resume_data["layout"]["section_titles"]["summary"], "Summary")

    def test_reject_flips_status_without_resume_change(self):
        message = _message_stub()
        resume = _resume_stub()
        db, out, resume_data = self._run(message, resume, merged={"basics": {"name": "合并名"}}, action="reject")
        self.assertEqual(out.action_status, "rejected")
        self.assertIsNone(resume_data)
        self.assertEqual(resume.resume_data["basics"]["name"], "旧名")
        self.assertFalse(any(isinstance(item, ResumeVersion) for item in db.added))


def _make_preview_client(message, current_user_id=7):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resumes_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: _DbStub(message)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=current_user_id)
    return TestClient(app)


class PreviewPendingMessageTest(unittest.TestCase):
    _CURRENT = {"basics": {"name": "当前名", "title": "后端工程师"}}
    _PENDING = {"basics": {"name": "待审名", "title": "后端工程师"}}

    def _client(self, message):
        resume = SimpleNamespace(
            id=3,
            language="zh-CN",
            resume_data=self._CURRENT,
            template_id="classic",
            template_config={},
        )
        client = _make_preview_client(message)
        patcher = patch.object(resumes_module, "get_resume", return_value=resume)
        patcher.start()
        self.addCleanup(patcher.stop)
        return client

    def test_pending_message_renders_snapshot(self):
        message = _message_stub(optimized_resume_data=self._PENDING)
        html = self._client(message).get("/api/resumes/3/preview-html", params={"message_id": 5})
        self.assertEqual(html.status_code, 200)
        self.assertIn("待审名", html.text)
        self.assertNotIn("当前名", html.text)

    def test_foreign_message_falls_back_to_current(self):
        message = _message_stub(user_id=999, optimized_resume_data=self._PENDING)
        html = self._client(message).get("/api/resumes/3/preview-html", params={"message_id": 5})
        self.assertEqual(html.status_code, 200)
        self.assertIn("当前名", html.text)

    def test_missing_message_falls_back_to_current(self):
        html = self._client(None).get("/api/resumes/3/preview-html", params={"message_id": 404})
        self.assertEqual(html.status_code, 200)
        self.assertIn("当前名", html.text)

    def test_without_message_id_renders_current(self):
        html = self._client(_message_stub(optimized_resume_data=self._PENDING)).get("/api/resumes/3/preview-html")
        self.assertEqual(html.status_code, 200)
        self.assertIn("当前名", html.text)
