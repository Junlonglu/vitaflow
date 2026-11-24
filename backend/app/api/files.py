from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin, get_current_user, user_from_token
from app.core.signed_url import verify_signature
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


def _bearer_token(request: Request) -> str:
    """只从 Authorization 头取令牌。

    `<img>` 带不了头，所以图片走 URL 签名（`sig` + `exp`），**不再接受 query 里的
    登录 token**——那等于把 24 小时有效的账号凭据写进浏览器历史与代理日志（RF-025）。
    """
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


@router.get("/{object_name:path}")
def get_uploaded_file(
    object_name: str,
    request: Request,
    db: Session = Depends(get_db),
    sig: str | None = None,
    exp: str | None = None,
):
    """上传对象代理。

    头像/公告图是对外展示的素材，保持匿名可读；对话图片是私有的，必须登录
    且只能读自己上传的那一份 —— 对象名虽含 uuid，但 URL 一旦外泄（截图、
    日志、分享）就会永久可读。
    """
    if not is_allowed_object_name(object_name):
        raise HTTPException(status_code=404, detail="文件不存在")
    if is_private_object_name(object_name):
        # 两条合法路径：URL 短签名（浏览器 <img> 用），或 Bearer 令牌（接口调用）。
        if not verify_signature(object_name, sig, exp):
            user = user_from_token(db, _bearer_token(request))
            # 未授权与「不是你的文件」返回同一个 404，不泄露对象是否存在。
            if user is None or object_name not in owned_object_names(db, user.id, [object_name]):
                raise HTTPException(status_code=404, detail="文件不存在")
    return stream_uploaded_file(object_name)
