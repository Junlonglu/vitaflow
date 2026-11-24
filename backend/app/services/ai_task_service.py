from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_config import FlowPointTransaction
from app.models.ai_task import AiTask
from app.models.user import User
from app.services.ai.token_usage import merge_token_usage
from app.services.flow_points_service import (
    POINT_ZERO,
    add_point_transaction,
    point_amount,
    point_number,
    settle_task_flow_points,
)

logger = logging.getLogger(__name__)

# 执行结果（status）
TASK_PENDING = "pending"
TASK_SUCCESS = "success"
TASK_FAILED = "failed"

# 计费结果（billing_status），与 status 正交：
#   unbilled 已建任务、结算尚未完成（进程中断会停在这里，由清扫器接手）
#   billed   已按实际用量结清
#   refunded 执行失败且预扣点数已退回
#   failed   结算过程自身异常（内容可能已交付，需要人工核对）
#   none     不涉及平台计费（用户自带模型）
BILLING_UNBILLED = "unbilled"
BILLING_BILLED = "billed"
BILLING_REFUNDED = "refunded"
BILLING_FAILED = "failed"
BILLING_NONE = "none"

# 平台自己的任务，不是用户发起的动作：出现在「我的 AI 记录」里只会让用户
# 困惑（"chat_summary" 是什么？），也会让用户侧的调用次数虚高（RF-018）。
# 它们是真实的平台成本，所以只从用户视图里排除，统计与成本核算仍要算上。
INTERNAL_TASK_TYPES = frozenset({"chat_summary"})


def user_visible_task_clause():
    """用户侧列表的过滤条件。定义在这里，避免每个列表各写一遍漏一个。"""
    return AiTask.task_type.notin_(INTERNAL_TASK_TYPES)


def task_lease_deadline(now: datetime | None = None) -> datetime:
    base = now or datetime.now()
    return base + timedelta(seconds=int(settings.ai_task_lease_seconds))


def create_ai_task(
    db: Session,
    user_id: int,
    task_type: str,
    resume_id: int | None = None,
    input_data: dict[str, Any] | None = None,
    model_name: str | None = None,
    feature: str | None = None,
    turn_id: int | None = None,
    turn_slot: int | None = None,
) -> AiTask:
    # 有回合键才写槽位：历史/非对话任务留给 NULL，唯一索引对 NULL 不生效，
    # 不会因为这些行互相冲突。
    slot = turn_slot if turn_slot is not None else (0 if turn_id is not None else None)
    task = AiTask(
        user_id=user_id,
        resume_id=resume_id,
        task_type=task_type,
        feature=feature,
        input_data=input_data or {},
        model_name=model_name,
        status=TASK_PENDING,
        billing_status=BILLING_UNBILLED,
        # 没租约的 pending 行等于永久挂起：进程被杀、Node 超时、客户端断连都会让它
        # 停在这里，预扣的点数也跟着卡住。清扫器只认这一列。
        expires_at=task_lease_deadline(),
        turn_id=turn_id,
        turn_slot=slot,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def turn_tasks(db: Session, turn_id: int) -> list[AiTask]:
    """同一回合已落库的任务行，按槽位排序（计费幂等的判重依据）。"""
    return list(
        db.scalars(
            select(AiTask)
            .where(AiTask.turn_id == turn_id)
            .order_by(AiTask.turn_slot.asc(), AiTask.id.asc())
        )
    )



def _log_finish(task: AiTask) -> None:
    """一行可 grep 的收尾日志：`turn_id=` 能把「消息 / 任务 / 流水」串起来。

    事后复盘一次故障回合，先 grep `turn_id=<消息 id>`，再看 `billing=` 与
    `points=`，就不用再去数据库里拼三张表（M5）。
    """
    logger.info(
        "任务收尾 task_id=%s turn_id=%s type=%s status=%s billing=%s points=%s",
        task.id,
        task.turn_id if task.turn_id is not None else "-",
        task.task_type,
        task.status,
        task.billing_status,
        point_number(task.points_used),
    )


def finish_ai_task(
    db: Session,
    task: AiTask,
    status: str,
    error_message: str | None = None,
    output_data: dict[str, Any] | None = None,
    token_usage: dict[str, Any] | None = None,
    settle: bool = True,
) -> None:
    # AI 服务可能复用过同一个 Session，事务可能停在失败状态。只在真的需要时
    # 回滚：无条件 rollback 会把调用方尚未提交的变更一起丢掉，这是个随时会
    # 被踩的坑（当前调用点恰好都先 commit 过，不代表以后也是）。
    if not db.is_active:
        db.rollback()
        refreshed = db.get(AiTask, task.id)
        if refreshed is None:
            return
        task = refreshed
    task.status = status
    task.error_message = error_message[:4000] if error_message else None
    task.output_data = output_data or {}
    if token_usage:
        task.input_data = merge_token_usage(task.input_data or {}, token_usage)
        task.tokens_used = int(task.input_data["token_usage"]["total_tokens"] or 0)
        transactions = db.scalars(
            select(FlowPointTransaction).where(FlowPointTransaction.task_id == task.id)
        ).all()
        for transaction in transactions:
            transaction.tokens_used = task.tokens_used
            db.add(transaction)
    task.update_time = datetime.now()

    if status == TASK_PENDING:
        # 非终态保持租约，留给清扫器收尾。
        task.billing_status = BILLING_UNBILLED
        task.expires_at = task.expires_at or task_lease_deadline()
        db.add(task)
        db.commit()
        _log_finish(task)
        return

    # 终态：租约完成使命，清空避免清扫器再捞到（终态判断仍以 status 为准）。
    task.expires_at = None

    if status == TASK_FAILED:
        _refund_failed_task_points(db, task)
        # 退款本身是幂等的（净额 >= 0 直接跳过），清扫器重复调用不会退两次。
        task.billing_status = BILLING_REFUNDED
        db.add(task)
        db.commit()
        _log_finish(task)
        return

    if not settle:
        # 用户自带模型：平台不扣点，billing_status 必须显式写 none，
        # 否则会被误判成「已交付未结算」，由清扫器补扣用户的钱。
        task.billing_status = BILLING_NONE
        db.add(task)
        db.commit()
        _log_finish(task)
        return

    # 交付结果与结算分两个事务落地：结算抛错时不能把 status 改回 failed——
    # 内容已经给到用户了，看板的成功率不该因此变差（RF-017）。
    db.add(task)
    db.commit()
    settle_task_billing(db, task)
    _log_finish(task)


def settle_task_billing(db: Session, task: AiTask) -> bool:
    """按实际用量结清任务，只改 billing_status。返回是否结清。

    失败的结算不抛给调用方：交付已经完成，这里唯一的后果是账目待核，
    记 failed 让看板与清扫器看得见即可。
    """
    try:
        settle_task_flow_points(db, task)
        task.billing_status = BILLING_BILLED
        db.add(task)
        db.commit()
        return True
    except Exception:  # noqa: BLE001 - 结算失败不能反向影响已交付的回合
        logger.exception("AI 任务结算失败，已标记待核 task_id=%s", task.id)
        db.rollback()
        refreshed = db.get(AiTask, task.id)
        if refreshed is None:
            return False
        refreshed.billing_status = BILLING_FAILED
        db.add(refreshed)
        db.commit()
        return False


def _refund_failed_task_points(db: Session, task: AiTask) -> None:
    """Return points for failed AI tasks once, keeping usage history auditable."""
    transactions = list(
        db.scalars(select(FlowPointTransaction).where(FlowPointTransaction.task_id == task.id))
    )
    net_points = sum((point_amount(item.points_delta) for item in transactions), POINT_ZERO)
    if net_points >= 0:
        task.points_used = POINT_ZERO
        return

    refund_points = abs(net_points)
    user = db.get(User, task.user_id)
    if not user:
        task.points_used = POINT_ZERO
        return

    # 余额变更必须交给数据库做增量：这里原来是 `user.flow_points = 旧值 + 退款`，
    # 同一个用户两个任务同时失败时会互相覆盖，只退一次（RF-015）。
    add_point_transaction(
        db,
        user,
        task.task_type,
        refund_points,
        "AI 任务失败，自动退还 Flow Points",
        task=task,
        tokens_used=int(task.tokens_used or 0),
    )
    # 退款流水落库后该任务的净额正好归零，显式写 0 而不是依赖净额推算。
    task.points_used = POINT_ZERO
    db.add(task)


def ai_task_usage_payload(task: AiTask) -> dict[str, Any]:
    return {
        "points_used": point_number(task.points_used),
        "tokens_used": int(task.tokens_used or 0),
        "token_usage": (task.input_data or {}).get("token_usage"),
    }


def expire_stale_pending_tasks(db: Session, *, now: datetime | None = None, limit: int = 200) -> int:
    """把租约过期的 pending 任务收尾：failed + 退款。返回处理条数。

    以 `status='pending' AND expires_at < now` 为唯一判据，配合条件 UPDATE 抢占，
    所以两个清扫器同时跑也不会退两次款（第二次 UPDATE 的 rowcount 是 0）。
    """
    moment = now or datetime.now()
    stale_ids = list(
        db.scalars(
            select(AiTask.id)
            .where(
                AiTask.status == TASK_PENDING,
                AiTask.expires_at.is_not(None),
                AiTask.expires_at < moment,
            )
            .order_by(AiTask.id.asc())
            .limit(limit)
        )
    )
    handled = 0
    for task_id in stale_ids:
        # 抢占：只有把 pending 改成 failed 的那个进程负责退款。
        claimed = db.execute(
            update(AiTask)
            .where(AiTask.id == task_id, AiTask.status == TASK_PENDING)
            .values(
                status=TASK_FAILED,
                expires_at=None,
                error_message="任务超时未结算，已自动结束并退回预扣点数",
                update_time=datetime.now(),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if not claimed.rowcount:
            continue
        task = db.get(AiTask, task_id)
        if task is None:
            continue
        db.refresh(task)
        _refund_failed_task_points(db, task)
        task.billing_status = BILLING_REFUNDED
        db.add(task)
        db.commit()
        handled += 1
    return handled


def settle_overdue_billed_tasks(db: Session, *, now: datetime | None = None, limit: int = 100) -> int:
    """补结算「已交付但没结完账」的任务。返回处理条数。

    交付与结算分两个事务：进程恰好死在两者之间时，任务停在 success/unbilled，
    已经没有租约（终态）也没有人再管它——用户用了服务但平台没收钱。
    """
    moment = now or datetime.now()
    overdue = list(
        db.scalars(
            select(AiTask)
            .where(
                AiTask.status == TASK_SUCCESS,
                AiTask.billing_status == BILLING_UNBILLED,
                AiTask.create_time < moment - timedelta(seconds=int(settings.ai_task_lease_seconds)),
            )
            .order_by(AiTask.id.asc())
            .limit(limit)
        )
    )
    handled = 0
    for task in overdue:
        settle_task_billing(db, task)
        handled += 1
    return handled
