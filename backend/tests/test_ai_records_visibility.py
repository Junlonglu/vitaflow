"""用户侧的「我的 AI 记录」不应出现平台内部任务。

`chat_summary` 是滚动摘要压缩产生的任务行：用户没有发起它，也不为它付点。
它混进列表时有很具体的坏处——用户看到一条不认识的调用，或者拿它去对照
自己的点数流水，发现对不上（RF-018）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.ai import my_ai_records
from app.core.database import Base
from app.models.ai_task import AiTask
from app.models.resume import Resume
from app.models.user import User


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Resume.__table__, AiTask.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _task(db, task_id: int, task_type: str, *, user_id: int = 1):
    db.add(
        AiTask(
            id=task_id,
            user_id=user_id,
            resume_id=None,
            task_type=task_type,
            status="success",
            billing_status="billed",
            points_used=Decimal("0"),
            create_time=datetime(2026, 11, 22, 10, task_id),
        )
    )


def _records(db, user: User, **kwargs):
    payload = my_ai_records(db=db, current_user=user, **kwargs)
    return payload["data"]


def test_internal_summary_tasks_are_hidden_from_user_records():
    db = _session()
    user = User(id=1, username="u1", email="u1@example.com", password_hash="x", flow_points=Decimal("0"))
    db.add(user)
    _task(db, 1, "ai_chat")
    _task(db, 2, "chat_summary")
    db.commit()

    data = _records(db, user)
    assert data["total"] == 1
    assert [item["task_type"] for item in data["items"]] == ["ai_chat"]


def test_total_matches_the_filtered_page():
    """计数与列表必须用同一个过滤条件，否则翻页会出现「第 2 页是空的」。"""
    db = _session()
    user = User(id=1, username="u1", email="u1@example.com", password_hash="x", flow_points=Decimal("0"))
    db.add(user)
    for index in range(1, 6):
        _task(db, index, "chat_summary")
    _task(db, 6, "ai_chat")
    _task(db, 7, "resume_score")
    db.commit()

    data = _records(db, user, page_size=1)
    assert data["total"] == 2
    assert len(data["items"]) == 1


def test_explicit_task_type_filter_still_works():
    db = _session()
    user = User(id=1, username="u1", email="u1@example.com", password_hash="x", flow_points=Decimal("0"))
    db.add(user)
    _task(db, 1, "ai_chat")
    _task(db, 2, "resume_score")
    db.commit()

    data = _records(db, user, task_type="resume_score")
    assert data["total"] == 1
    assert data["items"][0]["task_type"] == "resume_score"


def test_another_users_tasks_never_appear():
    db = _session()
    user = User(id=1, username="u1", email="u1@example.com", password_hash="x", flow_points=Decimal("0"))
    db.add(user)
    _task(db, 1, "ai_chat", user_id=2)
    db.commit()

    assert _records(db, user)["total"] == 0
