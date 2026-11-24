"""待确认提案的状态机：一个会话只有一条提案能被应用。

RF-006 的两种表现都在这份测试里：

1. 旧提案的内容已经过期——用户停留在旧提案上点「应用」，会把之后的新结果
   整份覆盖回去。修法是「新方案落库即把旧提案置 superseded」，从源头让它
   不再可应用；
2. apply 与 reject 并发——两边都读到 pending、都往下走。修法是把
   pending → applying 做成条件 UPDATE，rowcount 决定谁能继续。

状态机：pending → applying → applied / rejected；失败退回 pending（可重试）；
被新方案取代 → superseded；应用中途失联 → stale（由清扫器打）。
"""

import unittest
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.exceptions import AppException
from app.models.ai_chat import AiChatMessage, AiChatSession
from app.models.resume import Resume, ResumeVersion
from app.models.user import User
from app.services.ai_chat_service import (
    _claim_pending_message,
    _save_assistant_message,
    resolve_chat_change,
)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Resume.__table__,
            ResumeVersion.__table__,
            AiChatSession.__table__,
            AiChatMessage.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed(db):
    db.add(
        User(id=1, username="tester", email="t@example.com", password_hash="x")
    )
    db.add(
        Resume(
            id=2,
            user_id=1,
            title="我的简历",
            language="zh-CN",
            resume_data={"basics": {"name": "旧名"}},
            template_id="tech",
            template_config={"template_id": "tech"},
            revision=0,
        )
    )
    db.add(AiChatSession(id=3, user_id=1, resume_id=2, title="对话"))
    db.commit()
    return SimpleNamespace(id=1), db.get(Resume, 2), db.get(AiChatSession, 3)


def _assistant(db, message_id, *, data=None, status="pending"):
    message = AiChatMessage(
        id=message_id,
        session_id=3,
        user_id=1,
        resume_id=2,
        role="assistant",
        content="方案",
        optimized_resume_data=data or {"basics": {"name": f"方案{message_id}"}},
        action_status=status,
        create_time=datetime.now(),
        update_time=datetime.now(),
    )
    db.add(message)
    db.commit()
    return message


class SupersedeTest(unittest.TestCase):
    def test_new_proposal_supersedes_previous_pending(self):
        db = _session()
        user, resume, session = _seed(db)
        first = _assistant(db, 100)

        _save_assistant_message(
            db, session, user, resume, "新方案", [], {"basics": {"name": "新名"}}
        )

        db.expire_all()
        self.assertEqual(
            db.get(AiChatMessage, first.id).action_status,
            "superseded",
            "旧提案必须失效，否则它还能把新结果覆盖回去",
        )
        pending = list(
            db.scalars(
                select(AiChatMessage).where(AiChatMessage.action_status == "pending")
            )
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].optimized_resume_data["basics"]["name"], "新名")

    def test_reply_without_proposal_keeps_pending_untouched(self):
        db = _session()
        user, resume, session = _seed(db)
        first = _assistant(db, 100)
        _save_assistant_message(db, session, user, resume, "只是聊天", [], None)
        db.expire_all()
        self.assertEqual(db.get(AiChatMessage, first.id).action_status, "pending")


class ApplyClaimTest(unittest.TestCase):
    def test_claim_is_exclusive(self):
        # 并发下两个请求都通过了「状态是 pending」的检查，靠这条 UPDATE 分胜负：
        # 第二个的 rowcount 必须是 0，否则两个请求会同时往下写同一份提案。
        db = _session()
        _seed(db)
        message = _assistant(db, 700)
        self.assertTrue(_claim_pending_message(db, message.id))
        self.assertFalse(_claim_pending_message(db, message.id))
        db.expire_all()
        self.assertEqual(db.get(AiChatMessage, message.id).action_status, "applying")

    def test_superseded_proposal_cannot_be_applied(self):
        db = _session()
        user, resume, _ = _seed(db)
        message = _assistant(db, 200, status="superseded")
        with self.assertRaises(AppException) as ctx:
            resolve_chat_change(db, user, resume, message.id, "apply")
        self.assertEqual(ctx.exception.code, 409)

    def test_older_pending_loses_to_newer_pending(self):
        # 历史数据：本次改动之前可能已经堆了好几条 pending。
        db = _session()
        user, resume, _ = _seed(db)
        old = _assistant(db, 300)
        _assistant(db, 301)
        with self.assertRaises(AppException) as ctx:
            resolve_chat_change(db, user, resume, old.id, "apply")
        self.assertEqual(ctx.exception.code, 409)
        db.expire_all()
        self.assertEqual(
            db.get(AiChatMessage, old.id).action_status,
            "pending",
            "被拒绝的请求不能顺手改状态（用户还得能选新那条）",
        )

    def test_double_apply_is_rejected(self):
        db = _session()
        user, resume, _ = _seed(db)
        message = _assistant(db, 400)

        out, data = resolve_chat_change(db, user, resume, message.id, "apply")
        self.assertEqual(out.action_status, "applied")
        self.assertEqual(data["basics"]["name"], "方案400")

        with self.assertRaises(AppException) as ctx:
            resolve_chat_change(db, user, resume, message.id, "apply")
        self.assertEqual(ctx.exception.code, 409)

    def test_failed_apply_releases_claim_back_to_pending(self):
        db = _session()
        user, resume, _ = _seed(db)
        message = _assistant(db, 500)

        # 版本号对不上：写简历被拒，但提案本身没有错，必须还能重试。
        with self.assertRaises(AppException) as ctx:
            resolve_chat_change(db, user, resume, message.id, "apply", expect_revision=99)
        self.assertEqual(ctx.exception.code, 409)

        db.expire_all()
        self.assertEqual(db.get(AiChatMessage, message.id).action_status, "pending")
        self.assertEqual(db.get(Resume, 2).resume_data["basics"]["name"], "旧名")

    def test_reject_is_terminal_and_clears_fragments(self):
        db = _session()
        user, resume, _ = _seed(db)
        message = _assistant(db, 600)
        message.pending_info_fragments = [{"kind": "skill", "title": "Python"}]
        db.add(message)
        db.commit()

        out, data = resolve_chat_change(db, user, resume, message.id, "reject")
        self.assertEqual(out.action_status, "rejected")
        self.assertIsNone(data)

        db.expire_all()
        refreshed = db.get(AiChatMessage, message.id)
        self.assertEqual(refreshed.action_status, "rejected")
        self.assertIsNone(refreshed.pending_info_fragments)
        self.assertEqual(db.get(Resume, 2).resume_data["basics"]["name"], "旧名")
