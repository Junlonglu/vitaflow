"""生命周期清扫：非终态必须有租约，到期必须有人收尾。

M3 的不变量只有两条，但每条都要在真库语义下验证（条件 UPDATE 的 rowcount、
余额的事件式增减都不是 stub 能替代的）：

1. 任何非终态都有 `expires_at`；到期后任务变 failed、预扣点数回到原值，
   且重复清扫不会再退一次；
2. 提案与应用过程中的中间态会被收掉：过期 pending → superseded，
   失联 applying → stale，孤儿回合补一条 failed 占位任务（三张表重新对得上）。

另外验证「交付结果」与「计费结果」已经分开：结算抛错时 status 仍是 success。
"""

import asyncio
import threading
import time
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.ai_chat import AiChatMessage
from app.models.ai_config import FlowPointTransaction
from app.models.ai_task import AiTask
from app.models.user import User
from app.services import ai_task_service, maintenance_service


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
            AiTask.__table__,
            FlowPointTransaction.__table__,
            AiChatMessage.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _user(db, balance="100.00"):
    user = User(
        id=1,
        username="tester",
        email="tester@example.com",
        password_hash="x",
        flow_points=Decimal(balance),
    )
    db.add(user)
    db.commit()
    return user


def _task(db, **overrides):
    payload = {
        "id": 10,
        "user_id": 1,
        "task_type": "ai_chat",
        "status": ai_task_service.TASK_PENDING,
        "billing_status": ai_task_service.BILLING_UNBILLED,
        # 已过期的租约：这正是进程被杀之后库里留下的样子。
        "create_time": datetime.now() - timedelta(hours=1),
        "expires_at": datetime.now() - timedelta(minutes=5),
    }
    payload.update(overrides)
    task = AiTask(**payload)
    db.add(task)
    db.commit()
    return task


def _precharge(db, task, amount="5.00"):
    """模拟预扣：余额减少 + 一条负数流水（清扫器要退回的就是它）。"""
    user = db.get(User, task.user_id)
    user.flow_points = Decimal(user.flow_points) - Decimal(amount)
    db.add(
        FlowPointTransaction(
            id=100,
            user_id=user.id,
            task_id=task.id,
            feature_type=task.task_type,
            points_delta=-Decimal(amount),
            balance_after=user.flow_points,
            description="预扣",
        )
    )
    task.points_used = Decimal(amount)
    db.add(user)
    db.add(task)
    db.commit()


class CreateTaskLeaseTest(unittest.TestCase):
    def test_new_task_gets_lease_and_unbilled_status(self):
        db = _session()
        _user(db)
        task = ai_task_service.create_ai_task(db, 1, "ai_chat", turn_id=42)
        self.assertEqual(task.status, ai_task_service.TASK_PENDING)
        self.assertEqual(task.billing_status, ai_task_service.BILLING_UNBILLED)
        self.assertIsNotNone(task.expires_at, "非终态必须有租约，否则永远没人收尾")


class ExpireStaleTaskTest(unittest.TestCase):
    def test_expired_pending_task_is_failed_and_refunded_once(self):
        db = _session()
        user = _user(db)
        task = _task(db)
        _precharge(db, task, "5.00")
        self.assertEqual(user.flow_points, Decimal("95.00"))

        handled = ai_task_service.expire_stale_pending_tasks(db)
        self.assertEqual(handled, 1)

        db.expire_all()
        refreshed = db.get(AiTask, task.id)
        self.assertEqual(refreshed.status, ai_task_service.TASK_FAILED)
        self.assertEqual(refreshed.billing_status, ai_task_service.BILLING_REFUNDED)
        self.assertEqual(refreshed.points_used, Decimal("0.00"))
        self.assertIsNone(refreshed.expires_at)
        self.assertEqual(db.get(User, 1).flow_points, Decimal("100.00"), "点数必须回到原值")

        # 第二轮：状态已终态，不能再被捞到，也不能再退一次。
        self.assertEqual(ai_task_service.expire_stale_pending_tasks(db), 0)
        db.expire_all()
        self.assertEqual(db.get(User, 1).flow_points, Decimal("100.00"))

    def test_fresh_task_is_not_expired(self):
        db = _session()
        _user(db)
        _task(db, expires_at=datetime.now() + timedelta(minutes=10))
        self.assertEqual(ai_task_service.expire_stale_pending_tasks(db), 0)
        self.assertEqual(db.get(AiTask, 10).status, ai_task_service.TASK_PENDING)


class BillingSplitTest(unittest.TestCase):
    def test_settle_failure_keeps_status_success(self):
        db = _session()
        _user(db)
        task = _task(db, status=ai_task_service.TASK_SUCCESS, expires_at=None)
        with patch.object(
            ai_task_service, "settle_task_flow_points", side_effect=RuntimeError("账务超时")
        ):
            settled = ai_task_service.settle_task_billing(db, task)
        self.assertFalse(settled)
        db.expire_all()
        refreshed = db.get(AiTask, task.id)
        # 内容已经交付：执行结果不能被计费失败改写（RF-017）。
        self.assertEqual(refreshed.status, ai_task_service.TASK_SUCCESS)
        self.assertEqual(refreshed.billing_status, ai_task_service.BILLING_FAILED)

    def test_byok_task_is_not_treated_as_unbilled(self):
        db = _session()
        _user(db)
        task = _task(db, expires_at=None)
        with patch.object(ai_task_service, "settle_task_flow_points") as settle:
            ai_task_service.finish_ai_task(db, task, ai_task_service.TASK_SUCCESS, settle=False)
        settle.assert_not_called()
        db.expire_all()
        refreshed = db.get(AiTask, task.id)
        self.assertEqual(refreshed.billing_status, ai_task_service.BILLING_NONE)
        # 自带模型的任务绝不能落进「待补结算」，否则清扫器会替平台补扣用户点数。
        self.assertEqual(ai_task_service.settle_overdue_billed_tasks(db), 0)

    def test_overdue_delivered_task_is_settled_by_sweeper(self):
        db = _session()
        _user(db)
        task = _task(
            db,
            status=ai_task_service.TASK_SUCCESS,
            expires_at=None,
            create_time=datetime.now() - timedelta(hours=2),
        )
        with patch.object(ai_task_service, "settle_task_flow_points") as settle:
            handled = ai_task_service.settle_overdue_billed_tasks(db)
        self.assertEqual(handled, 1)
        settle.assert_called_once()
        db.expire_all()
        self.assertEqual(db.get(AiTask, task.id).billing_status, ai_task_service.BILLING_BILLED)


class ProposalSweepTest(unittest.TestCase):
    def _message(self, db, **overrides):
        payload = {
            "id": 7,
            "session_id": 3,
            "user_id": 1,
            "resume_id": 2,
            "role": "assistant",
            "content": "方案",
            "action_status": "pending",
            "create_time": datetime.now() - timedelta(days=2),
            "update_time": datetime.now() - timedelta(days=2),
        }
        payload.update(overrides)
        message = AiChatMessage(**payload)
        db.add(message)
        db.commit()
        return message

    def test_stale_pending_becomes_superseded(self):
        db = _session()
        _user(db)
        self._message(db)
        superseded, stale = maintenance_service.close_stale_proposals(db)
        self.assertEqual((superseded, stale), (1, 0))
        db.expire_all()
        self.assertEqual(db.get(AiChatMessage, 7).action_status, "superseded")

    def test_stuck_applying_becomes_stale(self):
        db = _session()
        _user(db)
        self._message(
            db,
            action_status="applying",
            create_time=datetime.now(),
            update_time=datetime.now() - timedelta(minutes=30),
        )
        superseded, stale = maintenance_service.close_stale_proposals(db)
        self.assertEqual((superseded, stale), (0, 1))
        db.expire_all()
        self.assertEqual(db.get(AiChatMessage, 7).action_status, "stale")

    def test_fresh_pending_is_untouched(self):
        db = _session()
        _user(db)
        self._message(db, create_time=datetime.now(), update_time=datetime.now())
        self.assertEqual(maintenance_service.close_stale_proposals(db), (0, 0))


class OrphanTurnTest(unittest.TestCase):
    def _user_message(self, db, message_id=20, created_minutes_ago=30):
        message = AiChatMessage(
            id=message_id,
            session_id=3,
            user_id=1,
            resume_id=2,
            role="user",
            content="帮我改一下",
            create_time=datetime.now() - timedelta(minutes=created_minutes_ago),
        )
        db.add(message)
        db.commit()
        return message

    def test_orphan_turn_gets_failed_placeholder_task(self):
        db = _session()
        _user(db)
        message = self._user_message(db)
        self.assertEqual(maintenance_service.close_orphan_turns(db), 1)

        task = db.scalar(select(AiTask).where(AiTask.turn_id == message.id))
        self.assertIsNotNone(task)
        self.assertEqual(task.status, ai_task_service.TASK_FAILED)
        self.assertEqual(task.billing_status, ai_task_service.BILLING_NONE)
        self.assertEqual(task.input_data["reason"], "orphan_turn")

        # 再跑一轮：已经有任务行，不能再补一条。
        self.assertEqual(maintenance_service.close_orphan_turns(db), 0)
        self.assertEqual(
            len(list(db.scalars(select(AiTask).where(AiTask.turn_id == message.id)))), 1
        )

    def test_turn_with_reply_is_not_orphan(self):
        db = _session()
        _user(db)
        message = self._user_message(db)
        db.add(
            AiChatMessage(
                id=21,
                session_id=3,
                user_id=1,
                resume_id=2,
                role="assistant",
                content="好的",
                create_time=datetime.now(),
            )
        )
        db.commit()
        self.assertEqual(maintenance_service.close_orphan_turns(db), 0)
        self.assertIsNone(db.scalar(select(AiTask).where(AiTask.turn_id == message.id)))


class SweepOnceTest(unittest.TestCase):
    def test_sweep_reports_each_counter_and_never_raises(self):
        db = _session()
        _user(db)
        task = _task(db)
        _precharge(db, task)
        counts = maintenance_service.sweep_once(db)
        for key in (
            "tasks_expired",
            "tasks_settled",
            "proposals_superseded",
            "proposals_stale",
            "orphan_turns",
            "orphan_uploads",
        ):
            self.assertIn(key, counts, f"清扫计数缺少 {key}")
            self.assertGreaterEqual(counts[key], 0, f"{key} 取值异常：{counts[key]}")
        self.assertEqual(counts["tasks_expired"], 1)
        snapshot = maintenance_service.last_sweep_snapshot()
        self.assertIn("finished_at", snapshot)
        self.assertEqual(snapshot["counts"]["tasks_expired"], 1)

    def test_one_failing_step_does_not_stop_the_rest(self):
        db = _session()
        _user(db)
        self._message = AiChatMessage(
            id=30,
            session_id=3,
            user_id=1,
            resume_id=2,
            role="user",
            content="hi",
            create_time=datetime.now() - timedelta(minutes=30),
        )
        db.add(self._message)
        db.commit()
        with patch.object(
            maintenance_service,
            "expire_stale_pending_tasks",
            side_effect=RuntimeError("boom"),
        ):
            counts = maintenance_service.sweep_once(db)
        # 失败项记 -1，其余照常执行——清扫器不能因为一类数据畸形就整体停摆。
        self.assertEqual(counts["tasks_expired"], -1)
        self.assertEqual(counts["orphan_turns"], 1)


class SweeperRunnerTest(unittest.IsolatedAsyncioTestCase):
    """清扫循环的启停：SIGTERM 时不能把半轮写入丢在身后（RF-045 的复发面）。"""

    async def test_stop_waits_for_inflight_round(self):
        started = threading.Event()
        finished = threading.Event()

        def slow_sweep():
            started.set()
            time.sleep(0.4)
            finished.set()

        runner = maintenance_service.MaintenanceSweeper(interval_seconds=60)
        with patch.object(maintenance_service, "_sweep_with_session", slow_sweep), patch.object(
            maintenance_service, "_acquire_lock", lambda ttl: True
        ):
            runner.start()
            # 等这一轮真的开始，再发停止信号
            await asyncio.to_thread(started.wait, 2)
            await runner.stop()

        self.assertTrue(finished.is_set(), "stop() 必须等当前这一轮跑完才返回")
        self.assertIsNone(runner._task, "停止后不能留下未取消的循环任务")

    async def test_stop_is_idempotent_when_never_started(self):
        runner = maintenance_service.MaintenanceSweeper(interval_seconds=60)
        await runner.stop()  # 没启动过也不该抛
