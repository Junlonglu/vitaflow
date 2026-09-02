from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_config import FlowPointTransaction
from app.models.ai_task import AiTask
from app.models.user import User
from app.services.ai.token_usage import merge_token_usage
from app.services.flow_points_service import POINT_ZERO, point_amount, point_number, settle_task_flow_points


def create_ai_task(
    db: Session,
    user_id: int,
    task_type: str,
    resume_id: int | None = None,
    input_data: dict[str, Any] | None = None,
    model_name: str | None = None,
) -> AiTask:
    task = AiTask(
        user_id=user_id,
        resume_id=resume_id,
        task_type=task_type,
        input_data=input_data or {},
        model_name=model_name,
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def finish_ai_task(
    db: Session,
    task: AiTask,
    status: str,
    error_message: str | None = None,
    output_data: dict[str, Any] | None = None,
    token_usage: dict[str, Any] | None = None,
) -> None:
    # AI services may have used the same session. Clear a failed transaction
    # before persisting the monitoring result, which was already committed at creation.
    db.rollback()
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
    if status == "success":
        settle_task_flow_points(db, task)
    elif status == "failed":
        _refund_failed_task_points(db, task)
    task.update_time = datetime.now()
    db.add(task)
    db.commit()


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

    user.flow_points = point_amount(user.flow_points) + refund_points
    refund = FlowPointTransaction(
        user_id=task.user_id,
        task_id=task.id,
        feature_type=task.task_type,
        points_delta=refund_points,
        balance_after=user.flow_points,
        tokens_used=int(task.tokens_used or 0),
        description="AI 任务失败，自动退还 Flow Points",
    )
    task.points_used = POINT_ZERO
    db.add(user)
    db.add(refund)


def ai_task_usage_payload(task: AiTask) -> dict[str, Any]:
    return {
        "points_used": point_number(task.points_used),
        "tokens_used": int(task.tokens_used or 0),
        "token_usage": (task.input_data or {}).get("token_usage"),
    }
