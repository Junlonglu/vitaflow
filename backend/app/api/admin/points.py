from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_admin
from app.models.ai_task import AiTask
from app.models.ai_config import FlowPointRule, FlowPointTransaction
from app.models.user import User
from app.services.flow_points_service import (
    add_point_transaction,
    ensure_default_point_rules,
    invalidate_point_rule_cache,
    point_amount,
    point_number,
)
from app.api.admin.common import _ai_model_config_lookups, _ai_task_model_filter, admin_task_model_info, page_data


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


GRANT_ALL_BATCH_PREFIX = "全员发放批次:"
GRANT_ALL_REVOKE_PREFIX = "撤回全员发放批次:"


def _grant_batch_no(request_id: str | None = None) -> str:
    """批次号。带 request_id 时由它派生，让同一次发放的重试落到同一批次。

    「同一操作」的判重不需要新表：batch_no 已经写在流水 description 里，把
    它变成请求键的纯函数之后，「这批发过没有」就是一次查询（RF-028）。
    """
    value = (request_id or "").strip()
    if value:
        return f"FG{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16].upper()}"
    return f"FG{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"


def _granted_user_ids(db: Session, batch_no: str) -> set[int]:
    """该批次已经发放过的用户 id。用于重试时跳过，也支持半途中断后续跑。"""
    prefix = f"{GRANT_ALL_BATCH_PREFIX}{batch_no}｜"
    return {
        int(user_id)
        for user_id in db.scalars(
            select(FlowPointTransaction.user_id).where(
                FlowPointTransaction.feature_type == "admin_grant_all",
                FlowPointTransaction.description.like(f"{prefix}%"),
            )
        )
    }


def _batch_description(prefix: str, batch_no: str, description: str) -> str:
    return f"{prefix}{batch_no}｜{description}"[:255]


def _extract_batch_no(description: str | None, prefix: str = GRANT_ALL_BATCH_PREFIX) -> str | None:
    text = (description or "").strip()
    if not text.startswith(prefix):
        return None
    value = text[len(prefix):].split("｜", 1)[0].strip().upper()
    return value or None


class UserPointsGrantAll(BaseModel):
    points: Decimal = Field(gt=0)
    description: str = "管理员统一发放 Flow Points"
    batch_size: int = Field(default=500, ge=1, le=2000)
    # 幂等键：一次「发放」的所有重试共用一个值（弹窗打开时生成）。双击、
    # 超时重发、刷新重试都会落到同一批次，不会重复加钱（RF-028）。
    request_id: str | None = Field(default=None, max_length=64)


class UserPointsGrantRevoke(BaseModel):
    description: str = "管理员撤回误发放 Flow Points"
    batch_size: int = Field(default=500, ge=1, le=2000)


class PointRuleWrite(BaseModel):
    display_name: str
    points_per_call: Decimal = Field(ge=0)
    points_per_1k_tokens: Decimal = Field(default=Decimal("0.00"), ge=0)
    points_per_million_tokens: Decimal = Field(default=Decimal("0.00"), ge=0)
    points_per_million_input_tokens: Decimal = Field(default=Decimal("0.00"), ge=0)
    points_per_million_output_tokens: Decimal = Field(default=Decimal("0.00"), ge=0)
    enabled: bool = True


@router.post("/users/flow-points/grant-all")
def grant_all_users_flow_points(
    payload: UserPointsGrantAll,
    db: Session = Depends(get_db),
):
    points = point_amount(payload.points)
    description = payload.description.strip() or "管理员统一发放 Flow Points"
    batch_no = _grant_batch_no(payload.request_id)
    transaction_description = _batch_description(GRANT_ALL_BATCH_PREFIX, batch_no, description)
    # 同一批次重试时，已经发过的人跳过：既避免重复加钱，也让中途失败的批次
    # 能续跑到结束，而不是「重试一次就宣布已发过」。
    granted_user_ids = _granted_user_ids(db, batch_no)
    total_count = 0
    batch_count = 0
    last_user_id = 0
    max_user_id = int(db.scalar(select(func.coalesce(func.max(User.id), 0)).where(User.status == "active")) or 0)

    while True:
        users = list(
            db.scalars(
                select(User)
                .where(User.status == "active", User.id > last_user_id, User.id <= max_user_id)
                .order_by(User.id.asc())
                .limit(payload.batch_size)
            )
        )
        if not users:
            break

        batch_count += 1
        for user in users:
            if int(user.id) in granted_user_ids:
                continue
            # 余额一律走条件 UPDATE（add_point_transaction）：这里原来是
            # 「读出来 + 点数再写回」，与并发的消费/撤回互相覆盖（RF-015）。
            add_point_transaction(db, user, "admin_grant_all", points, transaction_description)
            total_count += 1

        last_user_id = int(users[-1].id)
        db.commit()
        db.expunge_all()

    return success(
        {
            "count": total_count,
            "skipped": len(granted_user_ids),
            "replayed": bool(granted_user_ids),
            "points": point_number(points),
            "batch_size": payload.batch_size,
            "batches": batch_count,
            "batch_no": batch_no,
        },
        "Flow Points 已分批发放",
    )


@router.post("/users/flow-points/grant-all/{batch_no}/revoke")
def revoke_all_users_flow_points_grant(
    batch_no: str,
    payload: UserPointsGrantRevoke,
    db: Session = Depends(get_db),
):
    batch_no = batch_no.strip().upper()
    if not batch_no:
        raise AppException("发放批次不存在", 404)
    grant_prefix = f"{GRANT_ALL_BATCH_PREFIX}{batch_no}｜"
    revoke_prefix = f"{GRANT_ALL_REVOKE_PREFIX}{batch_no}｜"
    grant_exists = db.scalar(
        select(FlowPointTransaction.id)
        .where(
            FlowPointTransaction.feature_type == "admin_grant_all",
            FlowPointTransaction.points_delta > 0,
            FlowPointTransaction.description.like(f"{grant_prefix}%"),
        )
        .limit(1)
    )
    if not grant_exists:
        raise AppException("没有找到可撤回的全员发放批次", 404)
    revoked_exists = db.scalar(
        select(FlowPointTransaction.id)
        .where(
            FlowPointTransaction.feature_type == "admin_grant_all_revoke",
            FlowPointTransaction.description.like(f"{revoke_prefix}%"),
        )
        .limit(1)
    )
    if revoked_exists:
        raise AppException("该全员发放批次已经撤回，不能重复撤回", 409)

    description = payload.description.strip() or "管理员撤回误发放 Flow Points"
    transaction_description = _batch_description(GRANT_ALL_REVOKE_PREFIX, batch_no, description)
    total_count = 0
    batch_count = 0
    last_transaction_id = 0

    while True:
        grant_transactions = list(
            db.scalars(
                select(FlowPointTransaction)
                .where(
                    FlowPointTransaction.feature_type == "admin_grant_all",
                    FlowPointTransaction.points_delta > 0,
                    FlowPointTransaction.description.like(f"{grant_prefix}%"),
                    FlowPointTransaction.id > last_transaction_id,
                )
                .order_by(FlowPointTransaction.id.asc())
                .limit(payload.batch_size)
            )
        )
        if not grant_transactions:
            break

        users = {
            user.id: user
            for user in db.scalars(
                select(User).where(User.id.in_([item.user_id for item in grant_transactions]))
            )
        }
        batch_count += 1
        for grant_transaction in grant_transactions:
            user = users.get(grant_transaction.user_id)
            if not user:
                continue
            revoke_points = point_amount(grant_transaction.points_delta)
            # 撤回允许扣到 0 为止（allow_overdraft_to_zero）：余额不够时不发
            # 负数，但仍记全额 -revoke_points，保留「本该撤回多少」的事实。
            add_point_transaction(
                db,
                user,
                "admin_grant_all_revoke",
                -revoke_points,
                transaction_description,
                allow_overdraft_to_zero=True,
            )
            total_count += 1

        last_transaction_id = int(grant_transactions[-1].id)
        db.commit()
        db.expunge_all()

    return success(
        {
            "count": total_count,
            "batch_size": payload.batch_size,
            "batches": batch_count,
            "batch_no": batch_no,
        },
        "全员发放批次已撤回",
    )


@router.get("/flow-point-transactions")
def flow_point_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    direction: Optional[Literal["consume", "recharge"]] = None,
    feature_type: Optional[str] = None,
    model: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = select(FlowPointTransaction, User.username, User.email, AiTask).join(
        User, User.id == FlowPointTransaction.user_id
    ).outerjoin(
        AiTask, AiTask.id == FlowPointTransaction.task_id
    )
    count_query = select(func.count()).select_from(FlowPointTransaction).join(
        User, User.id == FlowPointTransaction.user_id
    ).outerjoin(
        AiTask, AiTask.id == FlowPointTransaction.task_id
    )
    filters = []
    if keyword.strip():
        pattern = f"%{keyword.strip()}%"
        filters.append(
            or_(
                User.username.like(pattern),
                User.email.like(pattern),
                FlowPointTransaction.description.like(pattern),
            )
        )
    if direction == "consume":
        filters.append(FlowPointTransaction.points_delta < 0)
    if direction == "recharge":
        filters.append(FlowPointTransaction.points_delta > 0)
    if feature_type:
        filters.append(FlowPointTransaction.feature_type == feature_type)
    model_filter = _ai_task_model_filter(db, model)
    if model_filter is not None:
        filters.append(AiTask.id.is_not(None))
        filters.append(model_filter)
    if user_id:
        filters.append(FlowPointTransaction.user_id == user_id)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    total = db.scalar(count_query) or 0
    rows = db.execute(
        query.order_by(FlowPointTransaction.create_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    configs_by_id, configs_by_model = _ai_model_config_lookups(db)
    grant_batch_nos = {
        batch_no
        for item, *_ in rows
        if item.feature_type == "admin_grant_all" and item.points_delta > 0
        for batch_no in [_extract_batch_no(item.description)]
        if batch_no
    }
    revoked_batch_nos: set[str] = set()
    if grant_batch_nos:
        revoke_filters = [
            FlowPointTransaction.description.like(f"{GRANT_ALL_REVOKE_PREFIX}{batch_no}｜%")
            for batch_no in grant_batch_nos
        ]
        revoked_descriptions = db.scalars(
            select(FlowPointTransaction.description).where(
                FlowPointTransaction.feature_type == "admin_grant_all_revoke",
                or_(*revoke_filters),
            )
        )
        revoked_batch_nos = {
            batch_no
            for description in revoked_descriptions
            for batch_no in [_extract_batch_no(description, GRANT_ALL_REVOKE_PREFIX)]
            if batch_no
        }
    feature_names = {
        "generate_resume": "AI 智能生成",
        "import_resume": "导入简历",
        "section_optimize": "AI 润色",
        "ai_chat": "AI 助手",
        "resume_score": "智能诊断",
        "jd_optimize": "JD 优化",
        "resume_translate": "简历翻译",
        "redeem": "兑换码充值",
        "admin_adjust": "管理员调整",
        "admin_grant_all": "全员发放",
        "admin_grant_all_revoke": "全员发放撤回",
        "signup_gift": "注册赠送",
    }
    items = [
        {
            "id": item.id,
            "user_id": item.user_id,
            "username": username,
            "email": email,
            "feature_type": item.feature_type,
            "feature_name": feature_names.get(item.feature_type, item.feature_type),
            "points_delta": point_number(item.points_delta),
            "balance_after": point_number(item.balance_after),
            "tokens_used": item.tokens_used or 0,
            **admin_task_model_info(task, configs_by_id, configs_by_model),
            "description": item.description or "",
            "grant_batch_no": _extract_batch_no(item.description, GRANT_ALL_BATCH_PREFIX)
            or _extract_batch_no(item.description, GRANT_ALL_REVOKE_PREFIX),
            "can_revoke": (
                item.feature_type == "admin_grant_all"
                and item.points_delta > 0
                and (batch_no := _extract_batch_no(item.description)) is not None
                and batch_no not in revoked_batch_nos
            ),
            "task_id": item.task_id,
            "direction": "recharge" if item.points_delta > 0 else "consume",
            "create_time": item.create_time,
        }
        for item, username, email, task in rows
    ]
    return success(page_data(items, total, page, page_size))


@router.get("/point-rules")
def point_rules(db: Session = Depends(get_db)):
    ensure_default_point_rules(db)
    items = list(db.scalars(select(FlowPointRule).order_by(FlowPointRule.id.asc())))
    return success(
        [
            {
                "id": item.id,
                "feature_type": item.feature_type,
                "display_name": item.display_name,
                "points_per_call": point_number(item.points_per_call),
                "points_per_1k_tokens": point_number(item.points_per_1k_tokens),
                "points_per_million_tokens": point_number(item.points_per_million_tokens),
                "points_per_million_input_tokens": point_number(item.points_per_million_input_tokens),
                "points_per_million_output_tokens": point_number(item.points_per_million_output_tokens),
                "enabled": item.enabled,
                "update_time": item.update_time,
            }
            for item in items
        ]
    )


@router.put("/point-rules/{feature_type}")
def update_point_rule(feature_type: str, payload: PointRuleWrite, db: Session = Depends(get_db)):
    ensure_default_point_rules(db)
    item = db.scalar(select(FlowPointRule).where(FlowPointRule.feature_type == feature_type))
    if not item:
        raise AppException("点数规则不存在", 404)
    item.display_name = payload.display_name.strip() or item.display_name
    item.points_per_call = point_amount(payload.points_per_call)
    item.points_per_1k_tokens = point_amount(0)
    input_rate = point_amount(payload.points_per_million_input_tokens)
    output_rate = point_amount(payload.points_per_million_output_tokens)
    if not input_rate and not output_rate and payload.points_per_million_tokens:
        input_rate = point_amount(payload.points_per_million_tokens)
        output_rate = point_amount(payload.points_per_million_tokens)
    item.points_per_million_input_tokens = input_rate
    item.points_per_million_output_tokens = output_rate
    item.points_per_million_tokens = max(input_rate, output_rate)
    item.enabled = payload.enabled
    db.add(item)
    db.commit()
    db.refresh(item)
    invalidate_point_rule_cache(feature_type)
    return success(
        {
            "feature_type": item.feature_type,
            "display_name": item.display_name,
            "points_per_call": point_number(item.points_per_call),
            "points_per_1k_tokens": point_number(item.points_per_1k_tokens),
            "points_per_million_tokens": point_number(item.points_per_million_tokens),
            "points_per_million_input_tokens": point_number(item.points_per_million_input_tokens),
            "points_per_million_output_tokens": point_number(item.points_per_million_output_tokens),
            "enabled": item.enabled,
        },
        "点数规则已更新",
    )
