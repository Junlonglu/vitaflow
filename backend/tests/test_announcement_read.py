"""公告已读的幂等性。

「首次已读」原来是 check-then-insert：双标签页或前端重试时，两个请求都判定
未读，第二个 INSERT 撞 (announcement_id, user_id) 唯一约束 → 未捕获
IntegrityError → 500。这里用真实 SQLite 会话跑两遍同一动作，确保第二次
既不报错也不重复计数。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.announcements import _insert_read_record, mark_announcement_read
from app.core.database import Base
from app.models.announcement import Announcement, AnnouncementRead


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _publish(db, announcement_id: int = 1) -> None:
    db.add(
        Announcement(
            id=announcement_id,
            title="系统公告",
            content="正文",
            status="published",
            created_by=1,
            read_count=0,
        )
    )
    db.commit()


def test_repeated_mark_is_idempotent(db):
    _publish(db)

    first = mark_announcement_read(db, 7, 1)
    second = mark_announcement_read(db, 7, 1)

    assert first == 1
    assert second == 1, "同一个用户重复已读不能把 read_count 加两次"
    assert db.query(AnnouncementRead).count() == 1


def test_second_insert_returns_false_instead_of_raising(db):
    """并发窗口里第二个插入必须被吞掉——这是 500 的唯一来源。

    线上真实的顺序是「两个请求都先判未读，然后一个先插成功」；这里直接构造
    那个后手：同一行再插一次，必须返回 False 而不是抛 IntegrityError。
    """
    _publish(db)

    assert _insert_read_record(db, 1, 7) is True
    db.commit()
    assert _insert_read_record(db, 1, 7) is False


def test_different_users_each_add_one(db):
    _publish(db)

    assert mark_announcement_read(db, 7, 1) == 1
    assert mark_announcement_read(db, 8, 1) == 2
    assert mark_announcement_read(db, 7, 1) == 2
    assert db.query(AnnouncementRead).count() == 2


def test_unpublished_announcement_is_404(db):
    _publish(db)
    db.get(Announcement, 1).status = "draft"
    db.commit()

    with pytest.raises(Exception) as ctx:
        mark_announcement_read(db, 7, 1)

    assert ctx.value.code == 404
    assert db.query(AnnouncementRead).count() == 0
