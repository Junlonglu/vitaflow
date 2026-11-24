"""运行指标：采样本身要准，且不能因为拿不到就报错。

指标代码出事的方式很具体——「接口 500」「永远 0」「把 -1 当有效值」。所以这里
不测「有没有这个字段」，测三件事：分位数算对、计数器不越界、拿不到水位时
降级而不是抛。
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import metrics
from app.core.database import Base
from app.models.ai_task import AiTask
from app.models.user import User


def _fresh_track() -> metrics.LatencyTrack:
    return metrics.LatencyTrack("test")


def test_latency_track_reports_p95_and_peak_in_milliseconds():
    track = _fresh_track()
    for value in range(1, 101):
        track.record(value / 1000)  # 1ms..100ms

    snapshot = track.snapshot()
    assert snapshot["count"] == 100
    assert snapshot["avg_ms"] == 50.5
    assert snapshot["max_ms"] == 100.0
    # p95 落在第 95 个样本附近（四舍五入到最近样本）
    assert 94.0 <= snapshot["p95_ms"] <= 96.0


def test_latency_track_windows_but_keeps_count():
    """采样窗口是滚动的，累计次数不是——否则「最近很慢」会被历史均值掩盖。"""
    track = _fresh_track()
    for _ in range(metrics.SAMPLE_WINDOW + 20):
        track.record(0.01)

    snapshot = track.snapshot()
    assert snapshot["samples"] == metrics.SAMPLE_WINDOW
    assert snapshot["count"] == metrics.SAMPLE_WINDOW + 20


def test_empty_track_reports_zero_instead_of_raising():
    snapshot = _fresh_track().snapshot()
    assert snapshot["count"] == 0
    assert snapshot["p95_ms"] == 0.0
    assert snapshot["avg_ms"] == 0.0


def test_pdf_queue_counts_waiting_then_running_then_idle():
    baseline = metrics.pdf_snapshot()

    metrics.pdf_render_waiting()
    metrics.pdf_render_waiting()
    waiting = metrics.pdf_snapshot()
    assert waiting["waiting"] == baseline["waiting"] + 2
    assert waiting["active"] == baseline["active"]

    metrics.pdf_render_started()
    metrics.pdf_render_started()
    running = metrics.pdf_snapshot()
    assert running["waiting"] == baseline["waiting"]
    assert running["active"] == baseline["active"] + 2

    metrics.pdf_render_finished()
    metrics.pdf_render_finished()
    idle = metrics.pdf_snapshot()
    assert idle["active"] == baseline["active"]
    assert idle["waiting"] == baseline["waiting"]


def test_pdf_counters_never_go_negative():
    """多减一次（异常路径重复收尾）不能把队列深度做成 -1。"""
    metrics.pdf_render_finished()
    metrics.pdf_render_started()
    snapshot = metrics.pdf_snapshot()
    assert snapshot["active"] >= 0
    assert snapshot["waiting"] >= 0


def test_measured_pool_records_checkout_wait(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}", poolclass=metrics.MeasuredQueuePool)
    before = metrics.pool_wait.snapshot()["count"]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()

    assert metrics.pool_wait.snapshot()["count"] > before


def test_pool_snapshot_survives_a_pool_without_status():
    class Broken:
        def status(self):
            raise RuntimeError("no status for you")

    snapshot = metrics.pool_snapshot(Broken())
    assert snapshot["status"] == "unavailable"
    assert "wait" in snapshot


def test_thread_snapshot_outside_event_loop_is_unavailable():
    assert metrics.thread_snapshot() == {"available": False}


def test_thread_snapshot_inside_event_loop_reports_capacity():
    async def main():
        return metrics.thread_snapshot()

    snapshot = asyncio.run(main())
    assert snapshot["available"] is True
    assert snapshot["total"] > 0
    assert snapshot["idle"] == snapshot["total"] - snapshot["borrowed"]


def test_counters_are_monotonic_per_name():
    metrics.increment("test_counter")
    metrics.increment("test_counter", 2)
    assert metrics.counters_snapshot()["test_counter"] >= 3


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, AiTask.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_metrics_endpoint_payload_uses_the_reconciliation_queries():
    """接口只做拼装：数字都来自 services，页面和接口不会各算一套。"""
    from app.api.admin.observability import observability_metrics

    db = _session()
    db.add(User(id=1, username="u1", email="u1@example.com", password_hash="x", flow_points=Decimal("0.00")))
    db.add(
        AiTask(
            id=1,
            user_id=1,
            turn_id=10,
            turn_slot=0,
            task_type="ai_chat",
            status="success",
            billing_status="billed",
            create_time=datetime.now() - timedelta(minutes=1),
        )
    )
    db.commit()

    payload = asyncio.run(observability_metrics(db))["data"]
    assert payload["tasks"]["turns"]["turns"] == 1
    assert payload["tasks"]["turns"]["delivered"] == 1
    assert payload["tasks"]["pending"]["total"] == 0
    assert "db_pool" in payload["runtime"]
    assert "pdf" in payload["runtime"]
    # 清扫器还没跑过时返回空 dict，前端据此显示「尚未运行」
    assert isinstance(payload["maintenance"], dict)
