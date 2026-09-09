"""删除简历的级联清理契约。

无外键约束：删除简历必须显式清掉对话消息、会话与版本快照（删除确认
弹窗已向用户告知「对话记录一并删除」），PDF 缓存文件同步清理。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.exceptions import AppException
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.resume import Resume, ResumeVersion
from app.services.resume_service import delete_resume


class _QueryStub:
    def __init__(self, model, deletes):
        self._model = model
        self._deletes = deletes

    def filter(self, *args, **kwargs):
        return self

    def delete(self, synchronize_session=None):
        self._deletes.append(self._model)


class _DbStub:
    def __init__(self, resume):
        self._resume = resume
        self.deletes = []
        self.deleted_rows = []

    def get(self, model, pk):
        return self._resume

    def query(self, model):
        return _QueryStub(model, self.deletes)

    def delete(self, obj):
        self.deleted_rows.append(obj)

    def commit(self):
        pass


def _resume():
    return SimpleNamespace(id=3, user_id=7, title="测试简历")


class DeleteResumeCascadeTest(unittest.TestCase):
    def test_delete_cleans_chat_versions_and_resume(self):
        resume = _resume()
        db = _DbStub(resume)
        with patch("app.services.resume_service.delete_cached_pdfs") as pdfs:
            delete_resume(db, 7, 3)
        self.assertEqual(
            db.deletes,
            [AiChatMessage, AiChatSession, ResumeVersion],
        )
        self.assertEqual(db.deleted_rows, [resume])
        pdfs.assert_called_once_with(3)

    def test_delete_rejects_foreign_resume(self):
        db = _DbStub(SimpleNamespace(id=3, user_id=999, title="别人的"))
        with self.assertRaises(AppException):
            delete_resume(db, 7, 3)
        self.assertEqual(db.deletes, [])
        self.assertEqual(db.deleted_rows, [])


if __name__ == "__main__":
    unittest.main()
