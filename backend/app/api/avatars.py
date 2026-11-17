"""头像库接口。

用户级：一个账号下的头像可以被任意一份简历复用。上传走既有的
``POST /api/files/upload-avatar``（上传即入库），这里只负责列出和移除。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_user
from app.models.user import User
from app.services import avatar_service as svc

router = APIRouter(prefix="/avatars", tags=["avatars"])


@router.get("")
def list_avatars(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return success(svc.list_avatars(db, current_user.id))


@router.delete("/{avatar_id}")
def delete_avatar(
    avatar_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 跨用户删除一律当作不存在：不要用 403 暴露「这个 id 别人在用」。
    svc.delete_avatar(db, current_user.id, avatar_id)
    return success(None, "已从信息库移除")
