from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_admin
from app.models.ai_config import UserFeedback
from app.models.user import User
from app.services.email_service import send_feedback_result_email
from app.services.rich_text_service import sanitize_rich_text_html
from app.api.admin.common import page_data


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


class FeedbackStatusUpdate(BaseModel):
    status: Literal["open", "processing", "resolved", "closed"]
    admin_note: Optional[str] = None
    admin_reply: Optional[str] = Field(default=None, max_length=500000)


def apply_feedback_update(item: UserFeedback, payload: FeedbackStatusUpdate) -> None:
    item.status = payload.status
    if payload.admin_note is not None:
        item.admin_note = payload.admin_note.strip()
    if payload.admin_reply is not None:
        item.admin_reply = payload.admin_reply.strip() or None
        item.reply_time = datetime.now() if item.admin_reply else None


def feedback_update_data(item: UserFeedback) -> dict:
    return {
        "id": item.id,
        "status": item.status,
        "admin_note": item.admin_note,
        "admin_reply": item.admin_reply,
        "reply_time": item.reply_time,
        "update_time": item.update_time,
    }


@router.get("/feedbacks")
def feedbacks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = select(UserFeedback, User.username, User.email).join(User, User.id == UserFeedback.user_id)
    count_query = select(func.count()).select_from(UserFeedback)
    if status:
        query = query.where(UserFeedback.status == status)
        count_query = count_query.where(UserFeedback.status == status)
    total = db.scalar(count_query) or 0
    rows = db.execute(
        query.order_by(UserFeedback.create_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        {
            "id": item.id,
            "user_id": item.user_id,
            "username": username,
            "email": email,
            "category": item.category,
            "content": sanitize_rich_text_html(item.content, allow_images=True),
            "contact": item.contact,
            "status": item.status,
            "admin_note": item.admin_note,
            "admin_reply": item.admin_reply,
            "reply_time": item.reply_time,
            "create_time": item.create_time,
            "update_time": item.update_time,
        }
        for item, username, email in rows
    ]
    return success(page_data(items, total, page, page_size))


@router.patch("/feedbacks/{feedback_id}")
def update_feedback(feedback_id: int, payload: FeedbackStatusUpdate, db: Session = Depends(get_db)):
    item = db.get(UserFeedback, feedback_id)
    if not item:
        raise AppException("反馈不存在", 404)
    apply_feedback_update(item, payload)
    db.add(item)
    db.commit()
    db.refresh(item)
    return success(feedback_update_data(item), "反馈状态已更新")


@router.post("/feedbacks/{feedback_id}/send-email")
def send_feedback_email(feedback_id: int, payload: FeedbackStatusUpdate, db: Session = Depends(get_db)):
    item = db.get(UserFeedback, feedback_id)
    if not item:
        raise AppException("反馈不存在", 404)
    user = db.get(User, item.user_id)
    if not user:
        raise AppException("反馈用户不存在", 404)
    apply_feedback_update(item, payload)
    if not item.admin_reply:
        raise AppException("请先填写用户可见回复", 400)
    db.add(item)
    db.commit()
    db.refresh(item)
    send_feedback_result_email(user.email, item, user)
    data = feedback_update_data(item)
    data["email_sent"] = True
    return success(data, "处理结果邮件已发送")
