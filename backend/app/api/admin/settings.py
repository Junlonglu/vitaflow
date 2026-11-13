from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.response import success
from app.core.security import get_current_admin
from app.services.app_settings_service import get_admin_settings, set_setting


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


class AdminSettingsWrite(BaseModel):
    signup_gift_points: int = Field(default=0, ge=0)
    ai_records_hint: str = Field(default="", max_length=500)
    feedback_notify_email: str = Field(default="", max_length=200)
    redeem_daily_attempt_limit: int = Field(default=10, ge=0, le=1000)
    user_agreement: str = Field(default="", max_length=100000)


@router.get("/settings")
def admin_settings(db: Session = Depends(get_db)):
    return success(get_admin_settings(db))


@router.put("/settings")
def update_admin_settings(payload: AdminSettingsWrite, db: Session = Depends(get_db)):
    set_setting(db, "signup_gift_points", str(payload.signup_gift_points), "新注册用户赠送 Flow Points")
    set_setting(db, "ai_records_hint", payload.ai_records_hint.strip(), "AI 记录页余额提示")
    set_setting(db, "feedback_notify_email", payload.feedback_notify_email.strip(), "用户反馈通知邮箱")
    set_setting(
        db,
        "redeem_daily_attempt_limit",
        str(payload.redeem_daily_attempt_limit),
        "每个用户每天最多尝试兑换码次数，0 表示不限制",
    )
    set_setting(db, "user_agreement", payload.user_agreement.strip(), "用户协议与隐私政策")
    return success(get_admin_settings(db), "系统设置已更新")
