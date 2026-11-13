from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_admin
from app.models.ai_config import FlowPointRedeemCode, FlowPointRedeemRecord
from app.models.user import User
from app.services.flow_points_service import _normalize_redeem_code, generate_redeem_codes, import_redeem_codes, point_amount, point_number
from app.api.admin.common import page_data


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


class RedeemCodeGenerateRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=500)
    points: Decimal = Field(gt=0)
    price: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_count: int = Field(default=1, gt=0)
    ip_once: bool = False
    custom_codes: str = Field(default="", max_length=20000)
    expire_time: Optional[datetime] = None
    note: Optional[str] = None


class RedeemCodeStatusUpdate(BaseModel):
    status: Literal["active", "disabled"]


class RedeemCodeUpdate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    points: Decimal = Field(gt=0)
    price: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_count: int = Field(gt=0)
    ip_once: bool = False
    expire_time: Optional[datetime] = None
    status: Literal["active", "disabled"] = "active"
    note: Optional[str] = Field(default=None, max_length=2000)


class RedeemCodeImportRequest(BaseModel):
    text: str
    points: Decimal = Field(gt=0)
    price: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_count: int = Field(default=1, gt=0)
    ip_once: bool = False
    expire_time: Optional[datetime] = None
    note: Optional[str] = None


class RedeemCodeBatchPriceUpdate(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)
    batch_no: Optional[str] = Field(default=None, max_length=80)
    price: Decimal = Field(ge=0)


def redeem_code_data(item: FlowPointRedeemCode) -> dict:
    return {
        "id": item.id,
        "code": item.code,
        "batch_no": item.batch_no,
        "points": point_number(item.points),
        "price": point_number(getattr(item, "price", 0)),
        "total_count": item.total_count,
        "used_count": item.used_count,
        "ip_once": bool(item.ip_once),
        "expire_time": item.expire_time,
        "status": item.status,
        "note": item.note,
        "create_time": item.create_time,
        "update_time": item.update_time,
    }


@router.get("/redeem-codes")
def redeem_codes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    points: Optional[Decimal] = Query(None, gt=0),
    keyword: str = "",
    db: Session = Depends(get_db),
):
    query = select(FlowPointRedeemCode)
    count_query = select(func.count()).select_from(FlowPointRedeemCode)
    if status == "used":
        query = query.where(FlowPointRedeemCode.used_count >= FlowPointRedeemCode.total_count)
        count_query = count_query.where(FlowPointRedeemCode.used_count >= FlowPointRedeemCode.total_count)
    elif status:
        query = query.where(FlowPointRedeemCode.status == status)
        count_query = count_query.where(FlowPointRedeemCode.status == status)
    if points is not None:
        query = query.where(FlowPointRedeemCode.points == point_amount(points))
        count_query = count_query.where(FlowPointRedeemCode.points == point_amount(points))
    if keyword.strip():
        like = f"%{keyword.strip().upper()}%"
        query = query.where(FlowPointRedeemCode.code.like(like))
        count_query = count_query.where(FlowPointRedeemCode.code.like(like))
    total = db.scalar(count_query) or 0
    rows = list(
        db.scalars(
            query.order_by(FlowPointRedeemCode.create_time.desc(), FlowPointRedeemCode.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = [redeem_code_data(item) for item in rows]
    return success(page_data(items, total, page, page_size))


@router.get("/redeem-codes/export")
def export_redeem_codes(
    status: Optional[str] = None,
    points: Optional[Decimal] = Query(None, gt=0),
    keyword: str = "",
    db: Session = Depends(get_db),
):
    query = select(FlowPointRedeemCode)
    if status == "used":
        query = query.where(FlowPointRedeemCode.used_count >= FlowPointRedeemCode.total_count)
    elif status:
        query = query.where(FlowPointRedeemCode.status == status)
    if points is not None:
        query = query.where(FlowPointRedeemCode.points == point_amount(points))
    if keyword.strip():
        query = query.where(FlowPointRedeemCode.code.like(f"%{keyword.strip().upper()}%"))
    items = list(db.scalars(query.order_by(FlowPointRedeemCode.create_time.desc(), FlowPointRedeemCode.id.desc())))
    text = "\n".join(item.code for item in items)
    headers = {"Content-Disposition": 'attachment; filename="flow-point-codes.txt"'}
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8", headers=headers)


@router.post("/redeem-codes/import")
def import_redeem_code_text(
    payload: RedeemCodeImportRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    items = import_redeem_codes(
        db,
        admin_id=admin.id,
        codes=payload.text.splitlines(),
        points=payload.points,
        total_count=payload.total_count,
        ip_once=payload.ip_once,
        expire_time=payload.expire_time,
        note=payload.note,
        price=payload.price,
    )
    return success(
        [redeem_code_data(item) for item in items],
        f"已导入 {len(items)} 个兑换码",
    )


@router.post("/redeem-codes")
def create_redeem_codes(
    payload: RedeemCodeGenerateRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    items = generate_redeem_codes(
        db,
        admin_id=admin.id,
        count=payload.count,
        points=payload.points,
        total_count=payload.total_count,
        ip_once=payload.ip_once,
        custom_codes=payload.custom_codes.splitlines() if payload.custom_codes else None,
        expire_time=payload.expire_time,
        note=payload.note,
        price=payload.price,
    )
    return success(
        [redeem_code_data(item) for item in items],
        "兑换码已生成",
    )


@router.patch("/redeem-codes/batch-price")
def update_redeem_code_batch_price(
    payload: RedeemCodeBatchPriceUpdate,
    db: Session = Depends(get_db),
):
    note = payload.note.strip() if payload.note else ""
    batch_no = payload.batch_no.strip().upper() if payload.batch_no else ""
    if not note and not batch_no:
        raise AppException("请输入要匹配的备注或批次 ID", 400)
    price = point_amount(payload.price)
    filters = []
    if note:
        filters.append(FlowPointRedeemCode.note == note)
    if batch_no:
        filters.append(FlowPointRedeemCode.batch_no == batch_no)
    query = select(FlowPointRedeemCode).where(*filters)
    items = list(db.scalars(query))
    if not items:
        raise AppException("没有找到对应的兑换码", 404)
    code_ids = [item.id for item in items]
    for item in items:
        item.price = price
        db.add(item)
    records = list(db.scalars(select(FlowPointRedeemRecord).where(FlowPointRedeemRecord.code_id.in_(code_ids))))
    for record in records:
        record.price = price
        db.add(record)
    db.commit()
    return success(
        {
            "note": note,
            "batch_no": batch_no,
            "price": point_number(price),
            "codes": len(items),
            "redeemed": len(records),
        },
        f"已更新 {len(items)} 个兑换码价格",
    )


@router.patch("/redeem-codes/{code_id}/status")
def update_redeem_code_status(
    code_id: int,
    payload: RedeemCodeStatusUpdate,
    db: Session = Depends(get_db),
):
    item = db.get(FlowPointRedeemCode, code_id)
    if not item:
        raise AppException("兑换码不存在", 404)
    item.status = payload.status
    db.add(item)
    db.commit()
    return success({"id": item.id, "status": item.status}, "兑换码状态已更新")


@router.put("/redeem-codes/{code_id}")
def update_redeem_code(
    code_id: int,
    payload: RedeemCodeUpdate,
    db: Session = Depends(get_db),
):
    item = db.get(FlowPointRedeemCode, code_id)
    if not item:
        raise AppException("兑换码不存在", 404)

    normalized_code = _normalize_redeem_code(payload.code)
    duplicate = db.scalar(
        select(FlowPointRedeemCode.id).where(
            FlowPointRedeemCode.code == normalized_code,
            FlowPointRedeemCode.id != code_id,
        )
    )
    if duplicate:
        raise AppException("兑换码已存在", 409)
    if payload.total_count < item.used_count:
        raise AppException(f"可兑换人数不能小于已兑换人数（{item.used_count}）", 400)
    if item.used_count > 0 and normalized_code != item.code:
        raise AppException("已有兑换记录，不能修改兑换码", 409)

    item.code = normalized_code
    item.points = point_amount(payload.points)
    item.price = point_amount(payload.price)
    item.total_count = payload.total_count
    item.ip_once = payload.ip_once
    item.expire_time = payload.expire_time
    item.status = payload.status
    item.note = payload.note.strip() if payload.note and payload.note.strip() else None
    db.add(item)
    records = list(db.scalars(select(FlowPointRedeemRecord).where(FlowPointRedeemRecord.code_id == item.id)))
    for record in records:
        record.price = item.price
        db.add(record)
    db.commit()
    db.refresh(item)
    return success(redeem_code_data(item), "兑换码已更新")


@router.delete("/redeem-codes/{code_id}")
def delete_redeem_code(
    code_id: int,
    db: Session = Depends(get_db),
):
    item = db.get(FlowPointRedeemCode, code_id)
    if not item:
        raise AppException("兑换码不存在", 404)
    if item.used_count > 0:
        raise AppException("该兑换码已有兑换记录，不能删除，请改为下架", 409)

    db.delete(item)
    db.commit()
    return success({"id": code_id}, "兑换码已删除")
