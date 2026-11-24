import html
import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_user
from app.models.announcement import Announcement, AnnouncementRead
from app.models.user import User
from app.services.announcement_cache_service import announcement_cache_service


router = APIRouter(prefix="/announcements", tags=["announcements"])


def announcement_detail_data(item: Announcement) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "content": item.content,
        "read_count": item.read_count or 0,
        "published_at": item.published_at.isoformat() if item.published_at else None,
    }


def announcement_summary(content: str, limit: int = 120) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", content))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _has_read_record(db: Session, announcement_id: int, user_id: int) -> bool:
    return (
        db.scalar(
            select(AnnouncementRead.id).where(
                AnnouncementRead.announcement_id == announcement_id,
                AnnouncementRead.user_id == user_id,
            )
        )
        is not None
    )


def _insert_read_record(db: Session, announcement_id: int, user_id: int) -> bool:
    """写一条已读记录；已经存在则返回 False。

    「先 SELECT 判存在、再 INSERT」本身不致命，致命的是**只靠它**：两个请求
    （双标签页 / 前端重试）同时判定未读，第二个 INSERT 撞唯一约束，未捕获就
    变成 500。唯一约束才是正确答案，所以这里在 savepoint 里插入并捕获
    IntegrityError——冲突时只回滚这一条插入，外层事务还能继续用。
    """
    try:
        with db.begin_nested():
            db.add(AnnouncementRead(announcement_id=announcement_id, user_id=user_id))
        return True
    except IntegrityError:
        return False


def mark_announcement_read(db: Session, user_id: int, announcement_id: int) -> int:
    announcement = db.get(Announcement, announcement_id)
    if not announcement or announcement.status != "published":
        raise AppException("公告不存在", 404)
    # 已读是绝大多数请求（前端会轮询当前公告），先走一条 SELECT 的快路径，
    # 避免每次都为「必然冲突」的插入开 savepoint。
    if not _has_read_record(db, announcement_id, user_id) and _insert_read_record(db, announcement_id, user_id):
        # 计数用条件自增，不做「读出来 +1 再写回」：并发两个首次已读会丢一次。
        db.execute(
            update(Announcement)
            .where(Announcement.id == announcement_id)
            .values(read_count=func.coalesce(Announcement.read_count, 0) + 1)
        )
        db.commit()
        announcement_cache_service.invalidate()
        db.refresh(announcement)
    return int(announcement.read_count or 0)


@router.get("")
def announcement_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    cache_suffix = f"history:{page}:{page_size}"
    cached = announcement_cache_service.get_json(cache_suffix)
    if cached is not None:
        return success(cached)
    condition = Announcement.status == "published"
    total = db.scalar(select(func.count()).select_from(Announcement).where(condition)) or 0
    items = list(
        db.scalars(
            select(Announcement)
            .where(condition)
            .order_by(Announcement.published_at.desc(), Announcement.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    data = {
            "items": [
                {
                    "id": item.id,
                    "title": item.title,
                    "summary": announcement_summary(item.content),
                    "read_count": item.read_count or 0,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    announcement_cache_service.set_json(cache_suffix, data)
    return success(data)


@router.get("/current")
def current_announcement(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cached = announcement_cache_service.get_json("current")
    if cached is None:
        announcement = db.scalar(
            select(Announcement)
            .where(Announcement.status == "published")
            .order_by(Announcement.published_at.desc(), Announcement.id.desc())
            .limit(1)
        )
        cached = {"announcement": announcement_detail_data(announcement) if announcement else None}
        announcement_cache_service.set_json("current", cached)
    data = cached.get("announcement") if isinstance(cached, dict) else None
    if not data:
        return success(None)
    if announcement_cache_service.is_dismissed(current_user.id, int(data["id"])):
        return success(None)
    data = {**data, "read_count": mark_announcement_read(db, current_user.id, int(data["id"]))}
    return success(data)


@router.get("/{announcement_id}")
def announcement_detail(
    announcement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cached = announcement_cache_service.get_json(f"detail:{announcement_id}")
    if cached is not None:
        return success({**cached, "read_count": mark_announcement_read(db, current_user.id, announcement_id)})
    announcement = db.get(Announcement, announcement_id)
    if not announcement or announcement.status != "published":
        raise AppException("公告不存在", 404)
    data = announcement_detail_data(announcement)
    data["read_count"] = mark_announcement_read(db, current_user.id, announcement_id)
    announcement_cache_service.set_json(f"detail:{announcement_id}", data)
    return success(data)


@router.post("/{announcement_id}/dismiss")
def dismiss_announcement(
    announcement_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    announcement = db.get(Announcement, announcement_id)
    if not announcement or announcement.status != "published":
        raise AppException("公告不存在", 404)
    announcement_cache_service.dismiss(current_user.id, announcement_id)
    return success(True, "已设置不再提示")
