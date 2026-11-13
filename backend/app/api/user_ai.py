"""用户自带模型（BYOK）与自定义技能的 HTTP 接口。

设计要点：
- 密钥只进不出：任何读接口都只回掩码，明文只在探活与真正调用模型时出现。
- 一个用户可保存多条模型配置，其中一条为默认；对话可按 id 临时切换。
- 技能支持单文件、ZIP 包、整个文件夹三种导入，包内容一律按纯文本处理。
- 测试连接是显式动作，会真正发一次网络请求，因此单独做 SSRF 校验。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
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
_ALLOWED_PACKAGE_SUFFIXES = {".zip"}


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
        "reasoning_effort": credential.reasoning_effort,
        "is_default": credential.is_default,
        "enabled": credential.enabled,
        "last_verified_time": credential.last_verified_time,
        "update_time": credential.update_time,
    }


def _model_list(db: Session, user_id: int) -> dict[str, Any]:
    items = [_credential_dict(item) for item in svc.list_credentials(db, user_id)]
    return {
        "items": items,
        "total": len(items),
        "active": sum(1 for item in items if item["enabled"]),
        "default_id": next((item["id"] for item in items if item["is_default"]), None),
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


async def _run_probe(db: Session, user_id: int, payload: UserAiCredentialTestIn, credential_id: int | None = None):
    api_key = (payload.api_key or "").strip()
    stored = svc.get_credential(db, user_id, credential_id)
    if not api_key:
        api_key = decrypt_secret(stored.api_key_encrypted) if stored else ""
    if not api_key:
        raise AppException("请先填写 API Key", 400)

    ok, detail, models = await svc.probe_credential(
        payload.base_url,
        api_key,
        payload.model,
        timeout=settings.byok_probe_timeout_seconds,
    )
    if ok and stored and stored.base_url == payload.base_url.rstrip("/") and stored.model == payload.model:
        svc.mark_verified(db, stored)
    return success({"ok": ok, "detail": detail, "models": models})


# ---------------------------------------------------------------------------
# 模型配置（多条）
# ---------------------------------------------------------------------------


@router.get("/models")
def list_models(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return success(_model_list(db, current_user.id))


@router.post("/models")
def add_model(
    payload: UserAiCredentialIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    credential = svc.create_credential(db, current_user.id, payload)
    return success(_credential_dict(credential), "模型配置已添加")


@router.put("/models/{credential_id}")
def put_model(
    credential_id: int,
    payload: UserAiCredentialIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    credential = svc.update_credential(db, current_user.id, credential_id, payload)
    return success(_credential_dict(credential), "模型配置已保存")


@router.delete("/models/{credential_id}")
def remove_model(
    credential_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc.delete_credential(db, current_user.id, credential_id)
    return success(None, "模型配置已删除")


@router.post("/models/{credential_id}/default")
def make_model_default(
    credential_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    svc.set_default_credential(db, current_user.id, credential_id)
    return success(_model_list(db, current_user.id), "已设为默认模型")


@router.post("/models/{credential_id}/test")
async def test_model(
    credential_id: int,
    payload: UserAiCredentialTestIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _run_probe(db, current_user.id, payload, credential_id)


@router.post("/models/test")
async def test_unsaved_model(
    payload: UserAiCredentialTestIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _run_probe(db, current_user.id, payload)


# ---------------------------------------------------------------------------
# 旧单配置接口：保留给尚未升级的客户端，语义落在「默认配置」上。
# ---------------------------------------------------------------------------


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
    return await _run_probe(db, current_user.id, payload)


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------


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


def _import_package(db: Session, user_id: int, *, zip_bytes: bytes | None, entries: list[tuple[str, bytes]]):
    if zip_bytes is not None:
        files, skipped = svc.collect_zip_files(zip_bytes)
    elif entries:
        files, skipped = svc.collect_folder_files(entries)
    else:
        raise AppException("请选择 .zip 技能包或技能文件夹", 400)

    payloads, warnings = svc.parse_skill_package(files)
    created = [svc.create_skill(db, user_id, payload, source="package") for payload in payloads]
    return {
        "items": [_skill_dict(item) for item in created],
        "total": len(created),
        "skipped": skipped,
        "warnings": warnings,
    }


@router.post("/skills/import-package")
async def import_skill_package(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ZIP 包或 webkitdirectory 文件夹：按 SKILL.md 拆成多个技能。"""
    form = await request.form()
    zip_bytes: bytes | None = None
    entries: list[tuple[str, bytes]] = []
    for key, value in form.multi_items():
        filename = getattr(value, "filename", None)
        read = getattr(value, "read", None)
        if not filename or not callable(read):
            continue
        if key == "file":
            data = await read(svc.MAX_PACKAGE_COMPRESSED_BYTES + 1)
            if filename.lower().endswith(".zip"):
                zip_bytes = data
            else:
                # 单文件也允许走批量端点，统一按文本技能处理。
                entries.append((filename, data))
        elif key == "files":
            entries.append((filename, await read(svc.MAX_PACKAGE_SINGLE_FILE_BYTES + 1)))
    if zip_bytes is not None and len(zip_bytes) > svc.MAX_PACKAGE_COMPRESSED_BYTES:
        raise AppException("技能包压缩文件不能超过 5 MB", 400)

    result = _import_package(db, current_user.id, zip_bytes=zip_bytes, entries=entries)
    label = f"已导入 {result['total']} 个技能" if result["total"] != 1 else "技能已导入"
    return success(result, label)


@router.post("/skills/import")
async def import_skill(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filename = file.filename or "skill.md"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    raw = await file.read(max(_MAX_SKILL_FILE_BYTES, svc.MAX_PACKAGE_COMPRESSED_BYTES) + 1)
    if suffix in _ALLOWED_PACKAGE_SUFFIXES:
        result = _import_package(db, current_user.id, zip_bytes=raw, entries=[])
        return success(result, f"已导入 {result['total']} 个技能")
    if suffix not in _ALLOWED_SKILL_SUFFIXES:
        raise AppException("只支持 .md / .markdown / .txt / .zip 技能文件", 400)
    if len(raw) > _MAX_SKILL_FILE_BYTES:
        raise AppException("技能文件不能超过 64 KB", 400)
    content = svc._decode_text(raw)
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
