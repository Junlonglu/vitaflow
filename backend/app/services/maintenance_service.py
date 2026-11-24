"""生命周期清扫：把「没人收尾」的状态收掉。

系统的每个非终态都必须有租约和一个到期后负责收尾的进程，否则一次断连、
一次进程被杀就会留下永久的中间态——任务停在 pending、预扣的点数卡住、
提案一直显示「待确认」、用户消息等不到回复也没有计费记录（RF-009 / RF-040）。

四类回收都在这里，全部幂等：

1. 租约过期的 `pending` 任务 → `failed` + 退款；
2. 已交付但没结完账的 `success/unbilled` 任务 → 补结算（补不上记 `failed` 待核）；
3. 过期的待确认提案 → `superseded`；应用中途失联的 → `stale`；
4. 孤儿回合（用户消息没有回复也没有任务）→ 补一条 failed 占位任务。

设计取舍：
- 进程内 `asyncio` 任务，5 分钟一轮，不引调度框架（单 worker 部署足够）；
- 每轮先抢一个 Redis 锁（拿不到就跳过），但锁失败**不阻塞**清扫——收缩到
  数据库层的条件是幂等的，重复跑不会退两次款；
- 一个子任务抛错不影响其余子任务：清扫器本身不能因为某类数据畸形就整体停摆。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis import redis_client
from app.models.ai_chat import AiChatMessage
from app.models.ai_task import AiTask
from app.models.resume import UploadedFile
from app.services.ai_chat_service import (
    ACTION_APPLYING,
    ACTION_PENDING,
    ACTION_STALE,
    ACTION_SUPERSEDED,
)
from app.services.ai_task_service import (
    BILLING_NONE,
    TASK_FAILED,
    expire_stale_pending_tasks,
    settle_overdue_billed_tasks,
)
from app.services.storage.storage_service import delete_object_names, list_object_names

logger = logging.getLogger(__name__)

# 应用提案占用的最长时限：超过它说明执行「应用」的进程已经不在了。
APPLY_CLAIM_TIMEOUT_MINUTES = 15
# 孤儿上传对象的最小年龄：上传与建索引之间只有毫秒级间隔，留一天足够宽。
ORPHAN_UPLOAD_MIN_AGE_HOURS = 24
# 每轮最多处理多少条，避免首轮（历史积压）把一次清扫拖成几分钟。
ORPHAN_TURN_SCAN_LIMIT = 200
ORPHAN_TURN_LOOKBACK_HOURS = 24

SWEEP_LOCK_PREFIX = f"{settings.redis_key_prefix}:maintenance-sweep"

_last_sweep: dict[str, Any] = {}


def last_sweep_snapshot() -> dict[str, Any]:
    """最近一轮清扫的结果，供管理端与排障查看（M5 的指标来源）。"""
    return dict(_last_sweep)


def _run_step(name: str, counts: dict[str, int], step: Callable[[], int]) -> None:
    """单个子任务失败只记日志，不能让整轮清扫中断。"""
    try:
        counts[name] = int(step())
    except Exception:  # noqa: BLE001 - 清扫器必须活到下一轮
        logger.exception("清扫子任务失败：%s", name)
        counts[name] = -1


def close_stale_proposals(db: Session, *, now: datetime | None = None) -> tuple[int, int]:
    """过期提案置 superseded、失联的 applying 置 stale。返回 (superseded, stale)。"""
    moment = now or datetime.now()
    proposal_cutoff = moment - timedelta(hours=int(settings.ai_pending_proposal_ttl_hours))
    superseded = db.execute(
        update(AiChatMessage)
        .where(
            AiChatMessage.action_status == ACTION_PENDING,
            AiChatMessage.create_time < proposal_cutoff,
        )
        .values(action_status=ACTION_SUPERSEDED, update_time=moment)
        .execution_options(synchronize_session=False)
    )
    claim_cutoff = moment - timedelta(minutes=APPLY_CLAIM_TIMEOUT_MINUTES)
    stale = db.execute(
        update(AiChatMessage)
        .where(
            AiChatMessage.action_status == ACTION_APPLYING,
            AiChatMessage.update_time < claim_cutoff,
        )
        .values(action_status=ACTION_STALE, update_time=moment)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return int(superseded.rowcount or 0), int(stale.rowcount or 0)


def close_orphan_turns(db: Session, *, now: datetime | None = None) -> int:
    """给「用户说了话、没有回复、也没有任务」的回合补一条 failed 占位任务。

    这样三张表就重新对得上：任何用户消息要么有助手回复，要么有一条任务行
    记录这次回合失败了，不存在「凭空消失、也没有账」的回合（RF-040）。
    """
    moment = now or datetime.now()
    cutoff = moment - timedelta(seconds=int(settings.ai_orphan_turn_grace_seconds))
    lookback = moment - timedelta(hours=ORPHAN_TURN_LOOKBACK_HOURS)
    candidates = list(
        db.scalars(
            select(AiChatMessage)
            .where(
                AiChatMessage.role == "user",
                AiChatMessage.create_time < cutoff,
                AiChatMessage.create_time > lookback,
            )
            .order_by(AiChatMessage.id.asc())
            .limit(ORPHAN_TURN_SCAN_LIMIT)
        )
    )
    closed = 0
    for message in candidates:
        replied = db.scalar(
            select(AiChatMessage.id)
            .where(
                AiChatMessage.session_id == message.session_id,
                AiChatMessage.role == "assistant",
                AiChatMessage.id > message.id,
            )
            .limit(1)
        )
        if replied is not None:
            continue
        charged = db.scalar(select(AiTask.id).where(AiTask.turn_id == message.id).limit(1))
        if charged is not None:
            continue
        db.add(
            AiTask(
                user_id=message.user_id,
                resume_id=message.resume_id,
                turn_id=message.id,
                turn_slot=0,
                task_type="ai_chat",
                status=TASK_FAILED,
                billing_status=BILLING_NONE,
                error_message="回合中断：没有助手回复，也没有计费记录",
                input_data={"source": "maintenance", "reason": "orphan_turn"},
            )
        )
        db.commit()
        closed += 1
    return closed


def cleanup_orphan_uploads(db: Session, *, now: datetime | None = None) -> int:
    """统计（并可选删除）没有索引行的 `ai-chat/` 上传对象。

    默认只统计不删：头像对象即便索引行已被用户删除，也可能仍被分享简历引用，
    自动删对象会直接打断外部访客看到的页面。`ai-chat/` 是唯一按回合产生、
    不参与分享的前缀，所以只扫它。
    """
    if not settings.maintenance_orphan_delete_enabled:
        return 0
    moment = now or datetime.now()
    cutoff = moment - timedelta(hours=ORPHAN_UPLOAD_MIN_AGE_HOURS)
    names = list_object_names("ai-chat/", before=cutoff)
    if not names:
        return 0
    indexed = set(
        db.scalars(select(UploadedFile.object_name).where(UploadedFile.object_name.in_(names))).all()
    )
    orphans = [name for name in names if name not in indexed]
    if not orphans:
        return 0
    logger.info("清扫发现 %s 个无索引的上传对象（ai-chat/）", len(orphans))
    delete_object_names(orphans)
    return len(orphans)


def sweep_once(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """跑一轮清扫，返回各类处理条数；失败项记 -1。"""
    moment = now or datetime.now()
    counts: dict[str, int] = {}
    _run_step("tasks_expired", counts, lambda: expire_stale_pending_tasks(db, now=moment))
    _run_step("tasks_settled", counts, lambda: settle_overdue_billed_tasks(db, now=moment))

    def _proposals() -> int:
        superseded, stale = close_stale_proposals(db, now=moment)
        counts["proposals_stale"] = stale
        return superseded

    _run_step("proposals_superseded", counts, _proposals)
    counts.setdefault("proposals_stale", 0)
    _run_step("orphan_turns", counts, lambda: close_orphan_turns(db, now=moment))
    _run_step("orphan_uploads", counts, lambda: cleanup_orphan_uploads(db, now=moment))

    _last_sweep.clear()
    _last_sweep.update(
        {
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "counts": dict(counts),
        }
    )
    if any(value for value in counts.values()):
        logger.info("清扫完成：%s", counts)
    return counts


def _sweep_with_session() -> dict[str, int]:
    db = SessionLocal()
    try:
        return sweep_once(db)
    finally:
        db.close()


def _acquire_lock(ttl_seconds: int) -> bool:
    """多 worker 时只让一个实例清扫；Redis 不可用则放行（清扫本身幂等）。"""
    try:
        return bool(redis_client.set(f"{SWEEP_LOCK_PREFIX}:lock", "1", nx=True, ex=ttl_seconds))
    except RedisError:
        logger.warning("清扫锁不可用，本轮照常执行（幂等）", exc_info=True)
        return True


class MaintenanceSweeper:
    """进程内清扫循环，随 `lifespan` 启停。

    SIGTERM 时 `lifespan` 会调用 `stop()`：置位停止事件并等待当前这一轮结束，
    不会把半轮的数据库写入丢在身后，留下「改了一半」的中间态。
    """

    def __init__(self, interval_seconds: int | None = None) -> None:
        self.interval = int(interval_seconds or settings.maintenance_sweep_interval_seconds)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if _acquire_lock(max(self.interval * 2, 60)):
                    await asyncio.to_thread(_sweep_with_session)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 清扫失败不能杀进程
                logger.exception("清扫轮次失败")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if not settings.maintenance_sweep_enabled:
            logger.info("清扫器已关闭（MAINTENANCE_SWEEP_ENABLED=false）")
            return
        if settings.app_env.strip().lower() in {"test", "testing"}:
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="maintenance-sweeper")
        logger.info("清扫器已启动，间隔 %s 秒", self.interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        finally:
            self._task = None
            logger.info("清扫器已停止")


sweeper = MaintenanceSweeper()
