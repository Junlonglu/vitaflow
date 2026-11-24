"""对账视图：四张表能不能对上。

这些断言的意义在于**它们能被改坏**：每条用例都对着一种过去的残缺态（RF-040），
把口径写错（比如按任务行数当回合数、把 BYOK 的 `none` 当成漏计费）就会立刻红。

全部走真 SQLite + 真 ORM：这些查询是手写 SQL，stub 掉就只剩「我以为它是这么写
的」这一层意思。
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.ai_chat import AiChatMessage
from app.models.ai_config import FlowPointTransaction
from app.models.ai_task import AiTask
from app.models.user import User
from app.services import reconciliation_service as recon

NOW = datetime(2026, 11, 22, 12, 0, 0)


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


def _user(db, user_id: int, points: str) -> User:
    user = User(
        id=user_id,
        username=f"u{user_id}",
        email=f"u{user_id}@example.com",
        password_hash="x",
        flow_points=Decimal(points),
    )
    db.add(user)
    return user


def _message(db, message_id: int, role: str, *, session_id: int = 1, user_id: int = 1, minutes_ago: int = 0):
    db.add(
        AiChatMessage(
            id=message_id,
            session_id=session_id,
            user_id=user_id,
            resume_id=1,
            role=role,
            content="x",
            create_time=NOW - timedelta(minutes=minutes_ago),
        )
    )


def _task(
    db,
    task_id: int,
    *,
    turn_id: int | None,
    slot: int = 0,
    status: str = "success",
    billing: str = "billed",
    points: str = "0",
    user_id: int = 1,
    minutes_ago: int = 0,
    expires_at: datetime | None = None,
):
    db.add(
        AiTask(
            id=task_id,
            user_id=user_id,
            resume_id=1,
            turn_id=turn_id,
            turn_slot=slot,
            task_type="ai_chat",
            status=status,
            billing_status=billing,
            points_used=Decimal(points),
            create_time=NOW - timedelta(minutes=minutes_ago),
            expires_at=expires_at,
        )
    )


def _transaction(db, task_id: int | None, delta: str, *, user_id: int = 1, balance: str = "0"):
    db.add(
        FlowPointTransaction(
            user_id=user_id,
            task_id=task_id,
            feature_type="ai_chat",
            points_delta=Decimal(delta),
            balance_after=Decimal(balance),
            create_time=NOW,
        )
    )


def _ledger(db, limit: int = 200):
    return recon.turn_ledger(db, since=NOW - timedelta(hours=24), limit=limit)


def _by_turn(ledger: dict) -> dict[int, dict]:
    return {row["turn_id"]: row for row in ledger["rows"]}


# ---------------------------------------------------------------------------
# 回合级账本
# ---------------------------------------------------------------------------


def test_ledger_classifies_the_four_broken_shapes():
    """四种残缺态各有名字；`ok` 必须真的只剩正常回合。"""
    db = _session()
    _user(db, 1, "100.00")

    # 101 完整：提问 + 回复 + 成功任务 + 结算 + 流水
    _message(db, 101, "user")
    _message(db, 102, "assistant")
    _task(db, 1, turn_id=101, points="10.00")
    _transaction(db, 1, "-10.00")

    # 201 没有回复
    _message(db, 201, "user", session_id=2)

    # 301 有回复、没有任务
    _message(db, 301, "user", session_id=3)
    _message(db, 302, "assistant", session_id=3)

    # 401 有回复、交付了、没结算
    _message(db, 401, "user", session_id=4)
    _message(db, 402, "assistant", session_id=4)
    _task(db, 2, turn_id=401, billing="unbilled", points="7.50")

    db.commit()

    ledger = _ledger(db)
    rows = _by_turn(ledger)
    assert rows[101]["state"] == "ok"
    assert rows[201]["state"] == "missing_reply"
    assert rows[301]["state"] == "missing_task"
    assert rows[401]["state"] == "unbilled"
    assert ledger["summary"] == {"total": 4, "ok": 1, "missing_reply": 1, "missing_task": 1, "unbilled": 1}


def test_ledger_only_counts_user_messages_as_turns():
    """助手消息不是回合：否则回合数直接翻倍。"""
    db = _session()
    _user(db, 1, "0.00")
    _message(db, 11, "user")
    _message(db, 12, "assistant")
    _message(db, 13, "assistant")
    db.commit()

    ledger = _ledger(db)
    assert [row["turn_id"] for row in ledger["rows"]] == [11]
    assert ledger["rows"][0]["replies"] == 2


def test_ledger_does_not_attribute_replies_from_another_session():
    """同会话按 id 往后算；别的会话的回复不能算进这一回合。"""
    db = _session()
    _user(db, 1, "0.00")
    _message(db, 11, "user", session_id=1)
    _message(db, 12, "assistant", session_id=2)
    db.commit()

    row = _ledger(db)["rows"][0]
    assert row["replies"] == 0
    assert row["state"] == "missing_reply"


def test_ledger_reports_points_gap_when_ledger_and_task_disagree():
    """任务说扣 5 点，流水只动了 3 点：差额必须显形。"""
    db = _session()
    _user(db, 1, "97.00")
    _message(db, 11, "user")
    _message(db, 12, "assistant")
    _task(db, 1, turn_id=11, points="5.00")
    _transaction(db, 1, "-3.00")
    db.commit()

    row = _ledger(db)["rows"][0]
    assert row["state"] == "ok"
    assert row["points_gap"] == pytest.approx(2.0)


def test_ledger_treats_byok_as_balanced():
    """BYOK（billing_status=none）本来就不扣点，不该被算成漏计费。"""
    db = _session()
    _user(db, 1, "0.00")
    _message(db, 11, "user")
    _message(db, 12, "assistant")
    _task(db, 1, turn_id=11, billing="none", points="0")
    db.commit()

    ledger = _ledger(db)
    assert ledger["rows"][0]["state"] == "ok"
    assert ledger["rows"][0]["points_gap"] == 0


# ---------------------------------------------------------------------------
# 余额与流水
# ---------------------------------------------------------------------------


def test_balance_check_finds_only_the_drifted_user():
    db = _session()
    _user(db, 1, "100.00")
    _transaction(db, None, "120.00")
    _transaction(db, None, "-20.00")
    # 用户 2 的余额被别处改过（绕过 add_point_transaction 的典型后果）
    _user(db, 2, "50.00")
    _transaction(db, None, "40.00", user_id=2)
    # 用户 3 没有任何流水
    _user(db, 3, "0.00")
    db.commit()

    result = recon.balance_mismatches(db)
    assert result["mismatched"] == 1
    assert result["users"][0]["user_id"] == 2
    assert result["users"][0]["gap"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 反向残缺：漏计费与幽灵任务
# ---------------------------------------------------------------------------


def test_missing_billing_finds_reply_without_task():
    db = _session()
    _user(db, 1, "0.00")
    _message(db, 11, "user")
    _message(db, 12, "assistant")
    # 第二回合有任务，不该出现在清单里
    _message(db, 21, "user", session_id=2)
    _message(db, 22, "assistant", session_id=2)
    _task(db, 1, turn_id=21, points="1.00")
    db.commit()

    missing = recon.missing_billing(db, since=NOW - timedelta(hours=24))
    assert [item["turn_id"] for item in missing] == [11]


def test_missing_billing_ignores_assistant_message_without_any_user_message():
    """没有前一条用户消息时没有 turn_id 可挂，不算漏计费（如系统问候）。"""
    db = _session()
    _user(db, 1, "0.00")
    _message(db, 12, "assistant")
    db.commit()

    assert recon.missing_billing(db, since=NOW - timedelta(hours=24)) == []


def test_ghost_task_is_reported():
    db = _session()
    _user(db, 1, "0.00")
    _message(db, 11, "user")
    _task(db, 1, turn_id=11)
    # 这条任务挂在一条不存在的消息上
    _task(db, 2, turn_id=999)
    db.commit()

    ghosts = recon.ghost_tasks(db)
    assert [item["task_id"] for item in ghosts] == [2]


# ---------------------------------------------------------------------------
# 在途任务与回合成功率
# ---------------------------------------------------------------------------


def test_pending_age_buckets_and_overdue():
    db = _session()
    _user(db, 1, "0.00")
    _task(db, 1, turn_id=None, status="pending", billing="unbilled", minutes_ago=0,
          expires_at=NOW + timedelta(minutes=10))
    _task(db, 2, turn_id=None, status="pending", billing="unbilled", minutes_ago=3,
          expires_at=NOW - timedelta(minutes=5))
    _task(db, 3, turn_id=None, status="pending", billing="unbilled", minutes_ago=20,
          expires_at=None)
    db.commit()

    snapshot = recon.pending_age(db, now=NOW)
    assert snapshot["total"] == 3
    assert snapshot["buckets"] == {"lt_1m": 1, "lt_5m": 1, "lt_15m": 0, "gte_15m": 1}
    assert snapshot["overdue"] == 1
    # 没有租约的非终态永远不会被收尾，必须单独计数
    assert snapshot["no_lease"] == 1
    assert snapshot["long_running"] == 1


def test_turn_success_dedupes_multi_model_turns():
    """一个回合两个模型（一成一败）：回合数算 1，成功率按「交付过」算 100%。"""
    db = _session()
    _user(db, 1, "0.00")
    _task(db, 1, turn_id=11, slot=0, status="success")
    _task(db, 2, turn_id=11, slot=1, status="failed")
    _task(db, 3, turn_id=12, slot=0, status="failed")
    db.commit()

    snapshot = recon.turn_success(db, since=NOW - timedelta(hours=24))
    assert snapshot["turns"] == 2
    assert snapshot["delivered"] == 1
    assert snapshot["success_rate"] == 50.0


def test_delivered_unbilled_excludes_byok_and_billed():
    db = _session()
    _user(db, 1, "0.00")
    _task(db, 1, turn_id=11, status="success", billing="unbilled", points="3.00")
    _task(db, 2, turn_id=12, status="success", billing="billed", points="3.00")
    _task(db, 3, turn_id=13, status="success", billing="none", points="0")
    _task(db, 4, turn_id=14, status="failed", billing="refunded", points="3.00")
    db.commit()

    result = recon.delivered_unbilled(db, since=NOW - timedelta(hours=24))
    assert result["total"] == 1
    assert result["items"][0]["turn_id"] == 11


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def test_reconcile_flags_unhealthy_and_names_the_reason():
    db = _session()
    _user(db, 1, "90.00")
    _message(db, 11, "user")
    _message(db, 12, "assistant")
    _task(db, 1, turn_id=11, points="10.00")
    # 少一条流水 → 余额对不上
    db.commit()

    result = recon.reconcile(db, hours=24, now=NOW)
    assert result["healthy"] is False
    assert result["unhealthy"]["balance_mismatch"] == 1


def test_reconcile_is_healthy_when_everything_lines_up():
    db = _session()
    _user(db, 1, "90.00")
    # 余额 90 = 充值 100 + 扣费 -10；没有充值行的话余额恒对不上，
    # 那是测试数据错，不是系统错。
    _transaction(db, None, "100.00", balance="100.00")
    _message(db, 11, "user")
    _message(db, 12, "assistant")
    _task(db, 1, turn_id=11, points="10.00")
    _transaction(db, 1, "-10.00", balance="90.00")
    db.commit()

    result = recon.reconcile(db, hours=24, now=NOW)
    assert result["healthy"] is True, result["unhealthy"]
    assert result["turns"]["summary"] == {"total": 1, "ok": 1}


def test_ledger_respects_limit_and_orders_newest_first():
    db = _session()
    _user(db, 1, "0.00")
    for index in range(5):
        _message(db, 100 + index, "user", session_id=index, minutes_ago=index)
    db.commit()

    rows = _ledger(db, limit=2)["rows"]
    # 按消息 id 倒序：最新的两条，且 limit 真的生效
    assert [row["turn_id"] for row in rows] == [104, 103]


def test_queries_work_without_any_rows():
    """空库不能让对账页 500：新部署第一天就该能打开。"""
    db = _session()
    result = recon.reconcile(db, hours=24, now=NOW)
    assert result["turns"]["summary"] == {"total": 0}
    assert result["healthy"] is True
    assert db.scalar(text("SELECT COUNT(*) FROM ai_tasks")) == 0
