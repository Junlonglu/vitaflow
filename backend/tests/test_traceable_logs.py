"""事后取证只需要 grep 两个键：`resume_id` + `revision`、`turn_id` + `billing`。

日志这种东西最容易被静默破坏——改个措辞、换个 f-string、把 logger 提到别的
模块，字段就没了，而没有任何测试会红。这里把「字段在日志里」钉住：它不是为了
好看，是因为没有它就只能连库手拼三张表（M5 的最后一项）。
"""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.ai_task import AiTask
from app.models.resume import Resume, ResumeVersion
from app.models.user import User
from app.services.ai_task_service import _log_finish
from app.services.resume_service import persist_resume_data


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Resume.__table__, ResumeVersion.__table__, AiTask.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_task_finish_log_carries_turn_id_and_billing(caplog):
    task = AiTask(
        id=7,
        user_id=1,
        turn_id=42,
        turn_slot=0,
        task_type="ai_chat",
        status="success",
        billing_status="billed",
        points_used=Decimal("3.50"),
    )
    with caplog.at_level(logging.INFO, logger="app.services.ai_task_service"):
        _log_finish(task)

    message = caplog.text
    assert "turn_id=42" in message
    assert "billing=billed" in message
    assert "status=success" in message
    assert "points=3.5" in message


def test_task_finish_log_marks_missing_turn_id_instead_of_printing_none(caplog):
    """非对话任务没有回合：打印 `-`，避免把 None 当成一个可 grep 的值。"""
    task = AiTask(
        id=8,
        user_id=1,
        turn_id=None,
        task_type="import_resume",
        status="failed",
        billing_status="refunded",
        points_used=Decimal("0"),
    )
    with caplog.at_level(logging.INFO, logger="app.services.ai_task_service"):
        _log_finish(task)

    assert "turn_id=-" in caplog.text


def test_resume_write_log_carries_resume_id_and_new_revision(caplog):
    db = _session()
    resume = Resume(
        id=5,
        user_id=1,
        title="简历",
        resume_data={"basics": {"name": "张三"}},
        template_id="classic",
        template_config={},
        revision=3,
    )
    db.add(resume)
    db.commit()

    with caplog.at_level(logging.INFO, logger="app.services.resume_service"):
        persist_resume_data(
            db,
            resume,
            {"basics": {"name": "李四"}},
            source="manual",
            expect_revision=3,
            user_id=1,
        )

    message = caplog.text
    assert "resume_id=5" in message
    # 4 是写后的新版本号：复盘要的是「第几版」，不是「预期第几版」
    assert "revision=4" in message
    assert "source=manual" in message


def test_resume_metadata_write_is_logged_too(caplog):
    """`data=None`（只改标题/模板）同样是写入，也要留痕。"""
    db = _session()
    resume = Resume(
        id=6,
        user_id=1,
        title="旧标题",
        resume_data={},
        template_id="classic",
        template_config={},
        revision=0,
    )
    db.add(resume)
    db.commit()

    with caplog.at_level(logging.INFO, logger="app.services.resume_service"):
        persist_resume_data(
            db,
            resume,
            None,
            source="manual",
            expect_revision=0,
            user_id=1,
            values={"title": "新标题", "update_time": datetime.now()},
        )

    assert "resume_id=6" in caplog.text
    assert "revision=1" in caplog.text
