from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.request_utils import get_client_ip
from app.core.response import success
from app.core.throttle import hit
from app.services.preview_service import render_resume_html
from app.services.resume_share_service import get_shared_resume, prepare_public_share_content


router = APIRouter(prefix="/shares", tags=["public-shares"])

# 公开端点的每 IP 请求上限。分享页只加载一次 HTML（图片走 /api/files），
# 120 次/分钟远高于正常刷新，却足以把「按 token 字典枚举」压到不划算。
SHARE_RATE_LIMIT = 120
SHARE_RATE_WINDOW_SECONDS = 60


@router.get("/{token}")
def shared_resume(token: str, request: Request, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    if hit("share", get_client_ip(request) or "unknown", SHARE_RATE_LIMIT, SHARE_RATE_WINDOW_SECONDS):
        raise AppException("请求过于频繁，请稍后再试", 429)
    resume = get_shared_resume(db, token)
    resume_data, template_config, title = prepare_public_share_content(resume)
    html = render_resume_html(resume_data, resume.template_id, template_config, resume.language)
    return success(
        {
            "title": title,
            "html": html,
            "expire_time": resume.share_expire_time,
        }
    )
