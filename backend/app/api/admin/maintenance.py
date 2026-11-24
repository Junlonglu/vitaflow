"""后台：生命周期清扫的可见性与手动触发。

清扫器默认每 5 分钟自己跑，这里回答两个运维问题：「上一轮清了什么」和
「现在能不能立刻清一次」。手动触发与定时任务走同一个 `sweep_once`，
所以两边的行为、计数口径完全一致。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin
from app.services.maintenance_service import last_sweep_snapshot, sweep_once

router = APIRouter(prefix="/maintenance", tags=["admin"], dependencies=[Depends(get_current_admin)])


def _config_payload() -> dict:
    return {
        "enabled": settings.maintenance_sweep_enabled,
        "interval_seconds": settings.maintenance_sweep_interval_seconds,
        "task_lease_seconds": settings.ai_task_lease_seconds,
        "proposal_ttl_hours": settings.ai_pending_proposal_ttl_hours,
        "orphan_turn_grace_seconds": settings.ai_orphan_turn_grace_seconds,
        "orphan_delete_enabled": settings.maintenance_orphan_delete_enabled,
    }


@router.get("/sweep")
def read_sweep():
    return success({"config": _config_payload(), "last": last_sweep_snapshot() or None})


@router.post("/sweep")
def run_sweep(db: Session = Depends(get_db)):
    counts = sweep_once(db)
    return success({"counts": counts, "last": last_sweep_snapshot()}, "清扫已完成")
