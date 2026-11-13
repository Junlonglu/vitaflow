"""用户自带模型（BYOK）与自定义技能的 HTTP 接口。

设计要点：
- 密钥只进不出：任何读接口都只回掩码，明文只在探活与真正调用模型时出现。
- 每个用户一条凭证、多个技能；所有查询都带 user_id，跨用户访问返回 404。
- 测试连接是显式动作，会真正发一次网络请求，因此单独做 SSRF 校验。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.user_ai import (
    UserAiCredentialIn,
    UserAiCredentialTestIn,
    UserSkillIn,
    UserSkillUpdateIn,
)
from app.services import user_ai_service as svc

router = APIRouter(prefix="/user-ai", tags=["user-ai"])

_MAX_SKILL_FILE_BYTES = 64 * 1024
_ALLOWED_SKILL_SUFFIXES = {".md", ".markdown", ".txt"}


def _credential_dict(credential: Any) -> dict[str, Any]:
    return {
        "id": credential.id,
        "provider_label": credential.provider_label,
        "base_url": credential.base_url,
        "model": credential.model,
        "api_key_masked": credential.api_key_masked,
        "has_api_key": bool(credential.api_key_encrypted),
        "context_window": credential.context_window,
        "max_tokens": credential.max_tokens,
        "supports_image": credential.supports_image,
        "enabled": credential.enabled,
        "last_verified_time": credential.last_verified_time,
        "update_time": credential.update_time,
    }


def _skill_dict(skill: Any) -> dict[str, Any]:
    return {
        "id": skill.id,
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "enabled": skill.enabled,
        "source": skill.source,
        "update_time": skill.update_time,
    }


@router.get("/credential")
def get_credential(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    credential = svc.get_credential(db, current_user.id)
    return success(_credential_dict(credential) if credential else None)


@router.put("/credential")
def put_credential(
    payload: UserAiCredentialIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    credential = svc.upsert_credential(db, current_user.id, payload)
    return success(_credential_dict(credential), "模型接入已保存")


@router.delete("/credential")
def remove_credential(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not svc.delete_credential(db, current_user.id):
        raise AppException("尚未配置模型接入", 404)
    return success(None, "已移除自定义模型，后续对话将使用平台模型")


@router.post("/credential/test")
async def test_credential(
    payload: UserAiCredentialTestIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api_key = (payload.api_key or "").strip()
    if not api_key:
        stored = svc.get_credential(db, current_user.id)
        api_key = decrypt_secret(stored.api_key_encrypted) if stored else ""
    if not api_key:
        raise AppException("请先填写 API Key", 400)

    ok, detail, models = await svc.probe_credential(
        payload.base_url,
        api_key,
        payload.model,
        timeout=settings.byok_probe_timeout_seconds,
    )
    if ok:
        stored = svc.get_credential(db, current_user.id)
        if stored and stored.base_url == payload.base_url.rstrip("/") and stored.model == payload.model:
            svc.mark_verified(db, stored)
    return success({"ok": ok, "detail": detail, "models": models})


@router.get("/skills")
def list_skills(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = [_skill_dict(item) for item in svc.list_skills(db, current_user.id)]
    active = sum(1 for item in items if item["enabled"])
    return success({"items": items, "total": len(items), "active": active})


@router.post("/skills")
def add_skill(
    payload: UserSkillIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = svc.create_skill(db, current_user.id, payload)
    return success(_skill_dict(skill), "技能已创建")


@router.post("/skills/import")
async def import_skill(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filename = file.filename or "skill.md"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _ALLOWED_SKILL_SUFFIXES:
        raise AppException("只支持 .md / .markdown / .txt 技能文件", 400)
    raw = await file.read(_MAX_SKILL_FILE_BYTES + 1)
    if len(raw) > _MAX_SKILL_FILE_BYTES:
        raise AppException("技能文件不能超过 64 KB", 400)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content = raw.decode("gbk")
        except UnicodeDecodeError as exc:
            raise AppException("技能文件编码无法识别，请另存为 UTF-8", 400) from exc
    payload = svc.parse_skill_file(content, filename)
    skill = svc.create_skill(db, current_user.id, payload, source="upload")
    return success(_skill_dict(skill), "技能已导入")


@router.patch("/skills/{skill_id}")
def patch_skill(
    skill_id: int,
    payload: UserSkillUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = svc.update_skill(db, current_user.id, skill_id, payload)
    return success(_skill_dict(skill), "技能已更新")


@router.delete("/skills/{skill_id}")
def remove_skill(
    skill_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc.delete_skill(db, current_user.id, skill_id)
    return success(None, "技能已删除")
