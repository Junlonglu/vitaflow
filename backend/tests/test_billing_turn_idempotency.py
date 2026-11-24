"""计费最小止血（S1 第 4 批）：回合幂等、退款原子性、全员发放幂等。

三条缺陷的共同点是「同一件事被做了两次或算了两遍」：

- RF-007：用量回调没有回合键，Node 重试一次就多建一条任务、多扣一次点。
- RF-015：退款「读余额 → 加回去 → 写回」，并发退款互相覆盖。
- RF-028：全员发放每次生成新批次号，双击就是两次发放。

这里锁定三件事：唯一索引真的在（且不误伤历史 NULL 行）、重复上报走重放、
余额变更不依赖调用方内存里的旧值、同一 request_id 的发放只加一次。
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.service_ticket_support import service_headers
from app.api import ai as ai_module
from app.api.admin import points as points_module
from app.api.admin.points import (
    GRANT_ALL_BATCH_PREFIX,
    UserPointsGrantAll,
    _grant_batch_no,
    grant_all_users_flow_points,
)
from app.api.ai import router as ai_router
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.exceptions import register_exception_handlers
from app.models.ai_config import FlowPointTransaction
from app.models.ai_task import AiTask
from app.models.user import User
from app.services.ai_task_service import finish_ai_task

_TURN_PAYLOAD = {
    "user_id": 7,
    "resume_id": 3,
    "feature": "chat",
    "usage": [
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "requests": 2,
            "input_tokens": 160,
            "output_tokens": 50,
            "total_tokens": 210,
        }
    ],
}


def _sqlite_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _task(**overrides) -> AiTask:
    base = {
        "id": 1,
        "user_id": 7,
        "task_type": "ai_chat",
        "status": "success",
        "points_used": Decimal("1.72"),
        "tokens_used": 210,
        "input_data": {
            "model": "deepseek/deepseek-v4-flash",
            "mapped": True,
            "model_config_id": 5,
            "requests": 2,
            "billing_mode": "platform",
        },
    }
    base.update(overrides)
    return AiTask(**base)


class _UsageDb(SimpleNamespace):
    """usage 端点用到的全部会话能力：取用户 + 按回合查任务。"""

    def __init__(self, tasks: list[AiTask] | None = None):
        super().__init__()
        self.tasks = list(tasks or [])
        self.commits = 0

    def get(self, model, _pk):
        if model is User:
            return SimpleNamespace(id=7, role="user", flow_points=0)
        return None

    def scalars(self, _stmt):
        return list(self.tasks)

    def add(self, _entity):
        return None

    def commit(self):
        self.commits += 1

    def refresh(self, _entity):
        return None


def _usage_client(tasks: list[AiTask] | None = None) -> tuple[TestClient, _UsageDb]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai_router, prefix="/api")
    db = _UsageDb(tasks)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db


@pytest.fixture(autouse=True)
def _token():
    original = settings.ai_service_token
    settings.ai_service_token = "test-token"
    yield
    settings.ai_service_token = original


def _report(client: TestClient, **overrides):
    payload = {**_TURN_PAYLOAD, **overrides}
    # 票据按请求体里的身份签：越权那条用例要验的是「回合归属」，不是票据本身。
    headers = service_headers(
        int(payload["user_id"]), payload.get("resume_id"), turn_id=payload.get("turn_id")
    )
    return client.post("/api/ai/service/usage", json=payload, headers=headers)


class TestTurnUniqueness:
    """唯一索引本身：同回合同槽位只能有一行，历史 NULL 行不受影响。"""

    def test_second_row_for_the_same_slot_is_rejected(self):
        _, db = _sqlite_session()
        db.add(_task(id=1, turn_id=55, turn_slot=0))
        db.add(_task(id=2, turn_id=55, turn_slot=0))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_different_slots_of_one_turn_coexist(self):
        _, db = _sqlite_session()
        db.add(_task(id=1, turn_id=55, turn_slot=0))
        db.add(_task(id=2, turn_id=55, turn_slot=1))
        db.commit()
        assert db.scalar(select(AiTask.id).where(AiTask.turn_id == 55)) is not None

    def test_legacy_rows_without_turn_id_do_not_collide(self):
        _, db = _sqlite_session()
        db.add(_task(id=1, turn_id=None, turn_slot=None))
        db.add(_task(id=2, turn_id=None, turn_slot=None))
        db.commit()
        assert len(db.scalars(select(AiTask.id)).all()) == 2


class TestUsageReplay:
    """同一 turn_id 再上报一次：不建任务、不扣点，返回首次结果。"""

    def test_repeat_report_replays_the_first_settlement(self):
        existing = _task(turn_id=55, turn_slot=0)
        client, _ = _usage_client([existing])

        with (
            patch.object(ai_module, "create_ai_task") as create,
            patch.object(ai_module, "finish_ai_task") as finish,
        ):
            resp = _report(client, turn_id=55)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["replayed"] is True
        create.assert_not_called()
        finish.assert_not_called()
        entry = data["entries"][0]
        assert entry["pi_model_ref"] == "deepseek/deepseek-v4-flash"
        assert entry["points_used"] == 1.72
        assert entry["tokens_used"] == 210
        assert entry["requests"] == 2, "重放必须给出与首次一致的 requests"

    def test_failed_turn_replays_as_empty_entries(self):
        failed = _task(turn_id=55, turn_slot=0, status="failed", input_data={"tool_calls": 1}, points_used=Decimal("0"))
        client, _ = _usage_client([failed])

        with patch.object(ai_module, "create_ai_task") as create:
            resp = _report(client, turn_id=55, status="failed", error_message="boom")

        assert resp.status_code == 200
        assert resp.json()["data"]["entries"] == []
        create.assert_not_called()

    def test_turn_owned_by_another_user_is_rejected(self):
        foreign = _task(turn_id=55, turn_slot=0, user_id=99)
        client, _ = _usage_client([foreign])

        with patch.object(ai_module, "create_ai_task") as create:
            resp = _report(client, turn_id=55)

        assert resp.status_code == 409
        create.assert_not_called()

    def test_new_turn_stamps_turn_id_and_slot_on_every_entry(self):
        client, _ = _usage_client([])
        created: list[dict] = []

        def fake_create(db, user_id, task_type, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(id=len(created), points_used=0, tokens_used=0, input_data={})

        payload = {
            **_TURN_PAYLOAD,
            "usage": [
                _TURN_PAYLOAD["usage"][0],
                {**_TURN_PAYLOAD["usage"][0], "model": "deepseek-reasoner"},
            ],
        }
        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", side_effect=fake_create),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, task, status, **kw: None),
        ):
            resp = client.post(
                "/api/ai/service/usage",
                json={**payload, "turn_id": 55},
                headers=service_headers(int(payload["user_id"]), payload.get("resume_id"), turn_id=55),
            )

        assert resp.status_code == 200
        assert [item["turn_slot"] for item in created] == [0, 1]
        assert {item["turn_id"] for item in created} == {55}

    def test_report_without_turn_id_still_works(self):
        client, _ = _usage_client([])
        with (
            patch.object(ai_module, "get_ai_config_by_model_ref", return_value=None),
            patch.object(ai_module, "create_ai_task", return_value=SimpleNamespace(id=1, points_used=0, tokens_used=0, input_data={})),
            patch.object(ai_module, "finish_ai_task", side_effect=lambda db, task, status, **kw: None),
        ):
            resp = _report(client)

        assert resp.status_code == 200
        assert resp.json()["data"]["entries"][0]["billed"] is True


class TestRefundUsesDatabaseBalance:
    """退款不能拿内存里的旧余额做加法（RF-015）。"""

    def test_refund_adds_to_the_stored_balance_not_the_stale_object(self):
        _, db = _sqlite_session()
        db.add(User(id=1, username="u", email="u@example.com", password_hash="x", flow_points=Decimal("100.00")))
        db.add(_task(id=1, user_id=1, status="pending"))
        db.add(
            FlowPointTransaction(
                id=1,
                user_id=1,
                task_id=1,
                feature_type="ai_chat",
                points_delta=Decimal("-5.00"),
                balance_after=Decimal("95.00"),
                tokens_used=0,
                description="AI 对话扣费",
            )
        )
        db.commit()

        user = db.get(User, 1)
        assert user.flow_points == Decimal("100.00")
        # 另一个并发回合先扣了 5：DB 现在是 95，但内存里的 user 还停在 100。
        db.execute(update(User).where(User.id == 1).values(flow_points=Decimal("95.00")).execution_options(synchronize_session=False))

        task = db.get(AiTask, 1)
        finish_ai_task(db, task, "failed", error_message="上游失败")

        db.refresh(user)
        assert user.flow_points == Decimal("100.00"), "退款应基于 DB 当前值 95+5，而不是 100+5"
        refund = db.scalar(
            select(FlowPointTransaction).where(
                FlowPointTransaction.task_id == 1,
                FlowPointTransaction.points_delta > 0,
            )
        )
        assert refund.points_delta == Decimal("5.00")
        assert refund.balance_after == Decimal("100.00"), "流水的 balance_after 必须等于真实余额"


class TestGrantAllIdempotency:
    """同一 request_id 的全员发放只加一次钱（RF-028）。"""

    def _seed_users(self, db) -> None:
        db.add_all(
            [
                User(id=1, username="u1", email="u1@example.com", password_hash="x", flow_points=Decimal("0.00")),
                User(id=2, username="u2", email="u2@example.com", password_hash="x", flow_points=Decimal("0.00")),
                User(id=3, username="u3", email="u3@example.com", password_hash="x", flow_points=Decimal("0.00")),
            ]
        )
        db.commit()

    def test_batch_no_is_a_pure_function_of_request_id(self):
        assert _grant_batch_no("req-1") == _grant_batch_no("req-1")
        assert _grant_batch_no("req-1") != _grant_batch_no("req-2")
        assert _grant_batch_no(None) != _grant_batch_no(None), "没有幂等键时必须回到随机批次号"

    def test_same_request_id_grants_each_user_once(self):
        _, db = _sqlite_session()
        self._seed_users(db)
        applied: list[int] = []

        def fake_apply(db_arg, user, feature_type, delta, description, **_kwargs):
            # 真实实现会写一条流水；SQLite 上 BigInteger 主键不自增，这里显式给 id。
            applied.append(int(user.id))
            db_arg.add(
                FlowPointTransaction(
                    id=len(applied),
                    user_id=user.id,
                    feature_type=feature_type,
                    points_delta=delta,
                    balance_after=delta,
                    description=description,
                )
            )

        with patch.object(points_module, "add_point_transaction", side_effect=fake_apply):
            first = grant_all_users_flow_points(UserPointsGrantAll(points=Decimal("10"), request_id="req-1"), db)
            second = grant_all_users_flow_points(UserPointsGrantAll(points=Decimal("10"), request_id="req-1"), db)

        assert applied == [1, 2, 3], "第二次请求不该再给任何人加点"
        assert first["data"]["count"] == 3
        assert second["data"]["count"] == 0
        assert second["data"]["skipped"] == 3
        assert second["data"]["replayed"] is True
        assert second["data"]["batch_no"] == first["data"]["batch_no"]
        rows = db.scalars(
            select(FlowPointTransaction).where(FlowPointTransaction.feature_type == "admin_grant_all")
        ).all()
        assert len(rows) == 3, "流水条数必须等于用户数，而不是两倍"

    def test_partial_batch_resumes_instead_of_being_skipped(self):
        _, db = _sqlite_session()
        self._seed_users(db)
        batch_no = _grant_batch_no("req-2")
        # 上一次发放发到第二个用户时中断：流水里只有 1、2。
        db.add_all(
            [
                FlowPointTransaction(
                    id=1,
                    user_id=1,
                    feature_type="admin_grant_all",
                    points_delta=Decimal("10.00"),
                    balance_after=Decimal("10.00"),
                    description=f"{GRANT_ALL_BATCH_PREFIX}{batch_no}｜运营活动",
                ),
                FlowPointTransaction(
                    id=2,
                    user_id=2,
                    feature_type="admin_grant_all",
                    points_delta=Decimal("10.00"),
                    balance_after=Decimal("10.00"),
                    description=f"{GRANT_ALL_BATCH_PREFIX}{batch_no}｜运营活动",
                ),
            ]
        )
        db.commit()
        applied: list[int] = []

        def fake_apply(db_arg, user, feature_type, delta, description, **_kwargs):
            applied.append(int(user.id))

        with patch.object(points_module, "add_point_transaction", side_effect=fake_apply):
            result = grant_all_users_flow_points(UserPointsGrantAll(points=Decimal("10"), request_id="req-2"), db)

        assert applied == [3], "只有第 3 个用户该拿到点数"
        assert result["data"]["count"] == 1
        assert result["data"]["skipped"] == 2

    def test_different_request_ids_grant_twice(self):
        _, db = _sqlite_session()
        self._seed_users(db)
        applied: list[int] = []

        def fake_apply(db_arg, user, feature_type, delta, description, **_kwargs):
            applied.append(int(user.id))

        with patch.object(points_module, "add_point_transaction", side_effect=fake_apply):
            grant_all_users_flow_points(UserPointsGrantAll(points=Decimal("10"), request_id="req-a"), db)
            grant_all_users_flow_points(UserPointsGrantAll(points=Decimal("10"), request_id="req-b"), db)

        assert applied == [1, 2, 3, 1, 2, 3]
