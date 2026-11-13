from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_admin
from app.models.resume import Resume
from app.models.user import User
from app.services.flow_points_service import add_point_transaction, point_number
from app.api.admin.common import page_data


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


class UserStateUpdate(BaseModel):
    status: Literal["active", "disabled"]


class UserPointsUpdate(BaseModel):
    points_delta: Decimal
    description: str = "管理员调整 Flow Points"


@router.get("/users")
def users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    status: Optional[str] = None,
    sort_by: str = Query("create_time"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    db: Session = Depends(get_db),
):
    resume_count = select(Resume.user_id, func.count(Resume.id).label("resume_count")).group_by(Resume.user_id).subquery()
    query = select(User, func.coalesce(resume_count.c.resume_count, 0)).outerjoin(resume_count, resume_count.c.user_id == User.id)
    count_query = select(func.count()).select_from(User)
    filters = []
    if keyword.strip():
        pattern = f"%{keyword.strip()}%"
        filters.append(or_(User.username.like(pattern), User.email.like(pattern)))
    if status:
        filters.append(User.status == status)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = db.scalar(count_query) or 0
    sort_columns = {
        "username": User.username,
        "email": User.email,
        "role": User.role,
        "resume_count": func.coalesce(resume_count.c.resume_count, 0),
        "flow_points": User.flow_points,
        "status": User.status,
        "create_time": User.create_time,
    }
    sort_column = sort_columns.get(sort_by, User.create_time)
    order_expr = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    rows = db.execute(
        query.order_by(order_expr, User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "avatar_url": user.avatar_url,
            "role": user.role,
            "status": user.status,
            "flow_points": point_number(user.flow_points),
            "resume_count": int(count),
            "create_time": user.create_time,
        }
        for user, count in rows
    ]
    return success(page_data(items, total, page, page_size))


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    payload: UserStateUpdate,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise AppException("用户不存在", 404)
    if user.id == admin.id:
        raise AppException("不能停用当前管理员账号", 403)
    if user.role == "admin":
        raise AppException("不能在此停用其他管理员", 403)
    user.status = payload.status
    db.commit()
    return success({"id": user.id, "status": user.status}, "用户状态已更新")


@router.patch("/users/{user_id}/flow-points")
def update_user_flow_points(
    user_id: int,
    payload: UserPointsUpdate,
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise AppException("用户不存在", 404)
    if payload.points_delta == 0:
        raise AppException("调整点数不能为 0", 400)
    add_point_transaction(
        db,
        user,
        "admin_adjust",
        payload.points_delta,
        payload.description.strip() or "管理员调整 Flow Points",
    )
    db.commit()
    db.refresh(user)
    return success({"id": user.id, "flow_points": point_number(user.flow_points)}, "Flow Points 已调整")


@router.get("/resumes")
def resumes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    db: Session = Depends(get_db),
):
    query = select(Resume, User.username, User.email).join(User, User.id == Resume.user_id)
    count_query = select(func.count()).select_from(Resume)
    if keyword.strip():
        pattern = f"%{keyword.strip()}%"
        condition = or_(Resume.title.like(pattern), User.username.like(pattern), User.email.like(pattern))
        query = query.where(condition)
        count_query = count_query.join(User, User.id == Resume.user_id).where(condition)
    total = db.scalar(count_query) or 0
    rows = db.execute(query.order_by(Resume.update_time.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = [
        {
            "id": resume.id,
            "title": resume.title,
            "user_id": resume.user_id,
            "username": username,
            "email": email,
            "language": resume.language,
            "template_id": resume.template_id,
            "create_time": resume.create_time,
            "update_time": resume.update_time,
        }
        for resume, username, email in rows
    ]
    return success(page_data(items, total, page, page_size))
