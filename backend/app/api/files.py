from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin, get_current_user, user_from_token
from app.models.user import User
from app.services.storage.storage_service import (
    is_allowed_object_name,
    is_private_object_name,
    owned_object_names,
    stream_uploaded_file,
    upload_ai_chat_image,
    upload_announcement_image,
    upload_avatar,
)

router = APIRouter(prefix="/files", tags=["files"])


@router.post("/upload-avatar")
def upload_avatar_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return success(upload_avatar(db, current_user.id, file))


@router.post("/upload-announcement-image")
def upload_announcement_image_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    return success(upload_announcement_image(db, current_admin.id, file))


@router.post("/upload-ai-chat-image")
def upload_ai_chat_image_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return success(upload_ai_chat_image(db, current_user.id, file))


@router.get("/font-assets/{file_name}")
def get_font_asset(file_name: str):
    safe_name = file_name.rsplit("/", 1)[-1]
    file_path = settings.backend_root / "app" / "static" / "fonts" / safe_name
    if safe_name != file_name or not file_path.is_file():
        raise HTTPException(status_code=404, detail="字体文件不存在")
    return FileResponse(file_path, media_type="font/woff2")


def _request_token(request: Request, query_token: str | None) -> str:
    """图片请求只能用 query token（<img> 带不了 Authorization 头），
    但接口调用方习惯用 Bearer，两种都收。"""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (query_token or "").strip()


@router.get("/{object_name:path}")
def get_uploaded_file(
    object_name: str,
    request: Request,
    token: str | None = None,
    db: Session = Depends(get_db),
):
    """上传对象代理。

    头像/公告图是对外展示的素材，保持匿名可读；对话图片是私有的，必须登录
    且只能读自己上传的那一份 —— 对象名虽含 uuid，但 URL 一旦外泄（截图、
    日志、分享）就会永久可读。
    """
    if not is_allowed_object_name(object_name):
        raise HTTPException(status_code=404, detail="文件不存在")
    if is_private_object_name(object_name):
        user = user_from_token(db, _request_token(request, token))
        # 未登录与「不是你的文件」返回同一个 404，不泄露对象是否存在。
        if user is None or object_name not in owned_object_names(db, user.id, [object_name]):
            raise HTTPException(status_code=404, detail="文件不存在")
    return stream_uploaded_file(object_name)
