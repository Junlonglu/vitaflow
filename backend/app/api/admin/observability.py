"""后台：运行指标与对账视图（M5「可证明性」的两个入口）。

两个接口分工明确：

* `/metrics` 回答「现在忙不忙、卡在哪」——池子等待、线程占用、PDF 队列、
  在途任务年龄，全是瞬时值；
* `/reconciliation` 回答「这几天的账对不对」——回合级账本 + 四类残缺清单，
  全是进数据库算出来的窗口值。

**为什么不合成一个**：一个要秒回、每次刷新都拉；一个要扫表、只在排查时拉。
合成一个的结果是前者被后者拖慢，然后大家都不用前者。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core import metrics
from app.core.database import engine, get_db
from app.core.response import success
from app.core.security import get_current_admin
from app.services.maintenance_service import last_sweep_snapshot
from app.services.reconciliation_service import (
    pending_age,
    reconcile,
    turn_success,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/observability", tags=["admin"], dependencies=[Depends(get_current_admin)])

# 这些计数是「本该为 0」的健康项，超窗口的统计没有意义——先看最近一天。
METRICS_BILLING_HOURS = 24


@router.get("/metrics")
async def observability_metrics(db: Session = Depends(get_db)):
    """六项最小指标集：池等待 / 线程占用 / PDF 队列 / 在途年龄 / 回合成功率 / 漏计费。

    `db_pool.wait` 与 `pdf.wait` 是滚动窗口（最近 512 次采样），不是全生命周期；
    `count` 才是累计次数。

    **必须是 async**：线程占用要从事件循环里取 anyio 的默认 limiter，同步端点
    会在线程池里跑，那里拿不到 limiter，指标永远是 `available=false`。
    数据库查询是短查询，放进事件循环的代价可以忽略。
    """
    since = datetime.now() - timedelta(hours=METRICS_BILLING_HOURS)
    return success(
        {
            "runtime": {
                "db_pool": metrics.pool_snapshot(engine.pool),
                "threads": metrics.thread_snapshot(),
                "pdf": metrics.pdf_snapshot(),
                "counters": metrics.counters_snapshot(),
            },
            "tasks": {
                "pending": pending_age(db),
                "turns": turn_success(db, since=since),
                "window_hours": METRICS_BILLING_HOURS,
            },
            "maintenance": last_sweep_snapshot(),
        }
    )


@router.get("/reconciliation")
def observability_reconciliation(
    hours: int = Query(24, ge=1, le=24 * 14),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """回合级对账。`healthy=false` 时看 `unhealthy` 里哪一项不为 0，再点进对应清单。"""
    return success(reconcile(db, hours=hours, limit=limit))
