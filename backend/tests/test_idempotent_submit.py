"""客户端幂等键：重复提交回放首次结果，不再多建一行。

三处最容易「双击出两份」的动作：新建简历、复制简历、提交反馈。前端在同一次
用户动作里复用同一个 `request_id`，后端把唯一约束 (user_id, request_id) 当兜底：

- 先查一次（快路径，普通的重试）；
- 查不到就插入，撞唯一约束说明是并发双击——回滚后回读赢家那一行。

第二条路径平时跑不到，但它才是「真的并发」时唯一生效的那条，所以单独覆盖。
"""


import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.feedback import FeedbackCreate, create_feedback
from app.core.database import Base
from app.models.ai_config import UserFeedback
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume import ResumeCreate
from app.services import resume_service


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Resume.__table__, UserFeedback.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _user(db):
    user = User(
        id=1,
        username="tester",
        email="t@example.com",
        password_hash="x",
        flow_points=Decimal("10.00"),
    )
    db.add(user)
    db.commit()
    return user


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model)) or 0


class ResumeCreateIdempotencyTest(unittest.TestCase):
    def test_same_request_id_returns_first_resume(self):
        db = _session()
        _user(db)
        first = resume_service.create_resume(db, 1, ResumeCreate(title="求职"), request_id="k1")
        second = resume_service.create_resume(db, 1, ResumeCreate(title="求职"), request_id="k1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(_count(db, Resume), 1)

    def test_missing_request_id_keeps_old_behaviour(self):
        # 没有幂等键时不合并：两条请求确实是两个动作（例如「再建一份」）。
        db = _session()
        _user(db)
        a = resume_service.create_resume(db, 1, ResumeCreate(title="A"))
        b = resume_service.create_resume(db, 1, ResumeCreate(title="B"))
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(_count(db, Resume), 2)

    def test_concurrent_duplicate_falls_back_to_existing_row(self):
        db = _session()
        _user(db)
        first = resume_service.create_resume(db, 1, ResumeCreate(title="求职"), request_id="k2")

        # 模拟并发：查重时两边都没看到对方，插入时撞唯一约束。
        real_lookup = resume_service._find_by_request_id
        with patch.object(
            resume_service,
            "_find_by_request_id",
            side_effect=[None, first],
        ):
            replay = resume_service.create_resume(
                db, 1, ResumeCreate(title="求职"), request_id="k2"
            )
        self.assertEqual(replay.id, first.id)
        self.assertEqual(_count(db, Resume), 1)
        self.assertIs(real_lookup, resume_service._find_by_request_id)

    def test_request_id_is_scoped_per_user(self):
        db = _session()
        _user(db)
        db.add(User(id=2, username="other", email="o@example.com", password_hash="x"))
        db.commit()
        mine = resume_service.create_resume(db, 1, ResumeCreate(title="A"), request_id="shared")
        theirs = resume_service.create_resume(db, 2, ResumeCreate(title="B"), request_id="shared")
        self.assertNotEqual(mine.id, theirs.id)


class ResumeDuplicateIdempotencyTest(unittest.TestCase):
    def test_same_request_id_returns_one_copy(self):
        db = _session()
        _user(db)
        source = resume_service.create_resume(db, 1, ResumeCreate(title="原件"))
        first = resume_service.duplicate_resume(db, 1, source.id, request_id="d1")
        second = resume_service.duplicate_resume(db, 1, source.id, request_id="d1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(_count(db, Resume), 2)

    def test_copy_is_still_a_deep_copy(self):
        db = _session()
        _user(db)
        source = resume_service.create_resume(db, 1, ResumeCreate(title="原件", resume_data={"basics": {"name": "A"}}))
        copy = resume_service.duplicate_resume(db, 1, source.id, request_id="d2")
        self.assertIsNot(copy.resume_data, source.resume_data)


class FeedbackIdempotencyTest(unittest.TestCase):
    def _submit(self, db, user, request_id):
        with patch("app.api.feedback.get_setting", return_value=""):
            response = create_feedback(
                FeedbackCreate(content="页面有点慢", request_id=request_id), db, user
            )
        # success() 返回的是可直接序列化的 dict（由路由层包成响应）。
        return response["data"]["id"]

    def test_same_request_id_returns_one_ticket(self):
        db = _session()
        user = _user(db)
        first = self._submit(db, user, "f1")
        second = self._submit(db, user, "f1")
        self.assertEqual(first, second)
        self.assertEqual(_count(db, UserFeedback), 1)

    def test_without_request_id_keeps_old_behaviour(self):
        db = _session()
        user = _user(db)
        first = self._submit(db, user, None)
        second = self._submit(db, user, None)
        self.assertNotEqual(first, second)
        self.assertEqual(_count(db, UserFeedback), 2)

    def test_notification_is_not_sent_twice(self):
        db = _session()
        user = _user(db)
        with patch("app.api.feedback.get_setting", return_value="ops@example.com"), patch(
            "app.api.feedback.send_feedback_notification"
        ) as notify:
            create_feedback(FeedbackCreate(content="第一条", request_id="f2"), db, user)
            create_feedback(FeedbackCreate(content="第一条", request_id="f2"), db, user)
        self.assertEqual(notify.call_count, 1, "回放不能重复打扰运维")
