from __future__ import annotations

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_admin
from app.models.announcement import Announcement
from app.models.user import User
from app.schemas.announcement import AnnouncementStatusUpdate, AnnouncementWrite
from app.services.announcement_service import announcement_has_text, sanitize_announcement_html
from app.services.announcement_cache_service import announcement_cache_service
from app.api.admin.common import page_data


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


def announcement_data(item: Announcement) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "content": item.content,
        "status": item.status,
        "read_count": item.read_count or 0,
        "created_by": item.created_by,
        "published_at": item.published_at,
        "create_time": item.create_time,
        "update_time": item.update_time,
    }


@router.get("/announcements")
def announcements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = select(Announcement)
    count_query = select(func.count()).select_from(Announcement)
    filters = []
    if keyword.strip():
        filters.append(Announcement.title.like(f"%{keyword.strip()}%"))
    if status:
        filters.append(Announcement.status == status)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = db.scalar(count_query) or 0
    items = list(
        db.scalars(
            query.order_by(Announcement.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return success(page_data([announcement_data(item) for item in items], total, page, page_size))


@router.post("/announcements")
def create_announcement(
    payload: AnnouncementWrite,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if not payload.title.strip():
        raise AppException("请填写公告标题", 400)
    content = sanitize_announcement_html(payload.content)
    if not announcement_has_text(content):
        raise AppException("请填写公告内容", 400)
    item = Announcement(
        title=payload.title.strip(),
        content=content,
        status=payload.status,
        created_by=admin.id,
        published_at=datetime.now() if payload.status == "published" else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    announcement_cache_service.invalidate()
    return success(announcement_data(item), "公告已创建")


@router.put("/announcements/{announcement_id}")
def update_announcement(
    announcement_id: int,
    payload: AnnouncementWrite,
    db: Session = Depends(get_db),
):
    item = db.get(Announcement, announcement_id)
    if not item:
        raise AppException("公告不存在", 404)
    if not payload.title.strip():
        raise AppException("请填写公告标题", 400)
    content = sanitize_announcement_html(payload.content)
    if not announcement_has_text(content):
        raise AppException("请填写公告内容", 400)
    was_published = item.status == "published"
    item.title = payload.title.strip()
    item.content = content
    item.status = payload.status
    if payload.status == "published" and not was_published:
        item.published_at = datetime.now()
    elif payload.status == "draft":
        item.published_at = None
    db.commit()
    db.refresh(item)
    announcement_cache_service.invalidate()
    return success(announcement_data(item), "公告已更新")


@router.patch("/announcements/{announcement_id}/status")
def update_announcement_status(
    announcement_id: int,
    payload: AnnouncementStatusUpdate,
    db: Session = Depends(get_db),
):
    item = db.get(Announcement, announcement_id)
    if not item:
        raise AppException("公告不存在", 404)
    item.status = payload.status
    item.published_at = datetime.now() if payload.status == "published" else None
    db.commit()
    db.refresh(item)
    announcement_cache_service.invalidate()
    return success(announcement_data(item), "公告状态已更新")


@router.delete("/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, db: Session = Depends(get_db)):
    item = db.get(Announcement, announcement_id)
    if not item:
        raise AppException("公告不存在", 404)
    db.delete(item)
    db.commit()
    announcement_cache_service.invalidate()
    return success(True, "公告已删除")
