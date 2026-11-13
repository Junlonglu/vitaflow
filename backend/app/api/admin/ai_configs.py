from __future__ import annotations

from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppException
from app.core.response import success
from app.core.security import get_current_admin
from app.models.ai_config import AiModelConfig
from app.services.ai_config_service import invalidate_ai_config_cache, invalidate_catalog_capabilities_cache
from app.services.ai_service_client import call_catalog
from app.services.flow_points_service import point_amount, point_number


router = APIRouter(prefix="", tags=["admin"], dependencies=[Depends(get_current_admin)])


class AiConfigWrite(BaseModel):
    """Model policy (ADR-0005): picks a catalog model and carries business knobs.

    Model identity and API keys live in the AI service catalog; this payload
    only references one via ``model_ref``.
    """

    name: str = Field(min_length=1, max_length=80)
    model_ref: Optional[str] = Field(default=None, max_length=200)
    context_messages: int = Field(default=12, ge=1, le=40)
    is_chat_selectable: bool = True
    sort_order: int = Field(default=100, ge=0, le=9999)
    chat_points_per_call: Optional[Decimal] = Field(default=None, ge=0)
    chat_points_per_million_input_tokens: Optional[Decimal] = Field(default=None, ge=0)
    chat_points_per_million_output_tokens: Optional[Decimal] = Field(default=None, ge=0)
    is_active: bool = True


class CatalogProviderWrite(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    access_key_id: str = Field(default="", max_length=500)
    secret_access_key: str = Field(default="", max_length=1000)


class CatalogModelWrite(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    reasoning: bool = False
    input: list[str] = Field(default=["text"])
    context_window: Optional[int] = Field(default=None, ge=1)
    max_tokens: Optional[int] = Field(default=None, ge=1)


class CatalogModelPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    reasoning: Optional[bool] = None
    input: Optional[list[str]] = None
    context_window: Optional[int] = Field(default=None, ge=1)
    max_tokens: Optional[int] = Field(default=None, ge=1)


def ai_config_data(item: AiModelConfig) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "model_ref": getattr(item, "model_ref", None) or None,
        "context_messages": item.context_messages,
        "is_chat_selectable": bool(getattr(item, "is_chat_selectable", True)),
        "sort_order": int(getattr(item, "sort_order", 100) or 100),
        "chat_points_per_call": point_number(item.chat_points_per_call) if item.chat_points_per_call is not None else None,
        "chat_points_per_million_input_tokens": point_number(item.chat_points_per_million_input_tokens) if item.chat_points_per_million_input_tokens is not None else None,
        "chat_points_per_million_output_tokens": point_number(item.chat_points_per_million_output_tokens) if item.chat_points_per_million_output_tokens is not None else None,
        "is_active": item.is_active,
        "create_time": item.create_time,
        "update_time": item.update_time,
    }


@router.get("/ai-configs")
def ai_configs(db: Session = Depends(get_db)):
    items = list(
        db.scalars(
            select(AiModelConfig).order_by(
                AiModelConfig.sort_order.asc(),
                AiModelConfig.is_active.desc(),
                AiModelConfig.update_time.desc(),
                AiModelConfig.id.desc(),
            )
        )
    )
    return success([ai_config_data(item) for item in items])


def _policy_payload(item: AiModelConfig, payload: AiConfigWrite) -> None:
    item.name = payload.name.strip()
    item.model_ref = (payload.model_ref or "").strip() or None
    item.context_messages = payload.context_messages
    item.is_chat_selectable = payload.is_chat_selectable
    item.sort_order = payload.sort_order
    item.chat_points_per_call = point_amount(payload.chat_points_per_call) if payload.chat_points_per_call is not None else None
    item.chat_points_per_million_input_tokens = point_amount(payload.chat_points_per_million_input_tokens) if payload.chat_points_per_million_input_tokens is not None else None
    item.chat_points_per_million_output_tokens = point_amount(payload.chat_points_per_million_output_tokens) if payload.chat_points_per_million_output_tokens is not None else None
    item.is_active = payload.is_active


def _validate_model_ref(model_ref: str | None) -> tuple[bool, str]:
    """Degrade-tolerant catalog check (ticket 11): reject unknown refs when the
    AI service is reachable, warn instead of blocking when it is not."""
    ref = (model_ref or "").strip()
    if not ref:
        return True, "未选择目录模型，保存后该配置不会参与对话"
    try:
        providers = call_catalog("GET", "/v1/catalog").get("providers") or []
    except Exception:
        return True, "AI 服务暂不可达，未能校验模型引用；运行时若模型不存在对话会报错"
    for provider in providers:
        for model in provider.get("models") or []:
            if model.get("ref") == ref:
                return True, ""
    return False, f"模型 {ref} 不在 AI 服务目录中，请先在模型目录中添加"


@router.post("/ai-configs")
def create_ai_config(payload: AiConfigWrite, db: Session = Depends(get_db)):
    ok, message = _validate_model_ref(payload.model_ref)
    if not ok:
        raise AppException(message, 400)
    if payload.is_active:
        db.query(AiModelConfig).update({AiModelConfig.is_active: False})
    item = AiModelConfig(
        name=payload.name.strip(),
        context_messages=payload.context_messages,
        is_chat_selectable=payload.is_chat_selectable,
        sort_order=payload.sort_order,
        model_ref=(payload.model_ref or "").strip() or None,
        chat_points_per_call=point_amount(payload.chat_points_per_call) if payload.chat_points_per_call is not None else None,
        chat_points_per_million_input_tokens=point_amount(payload.chat_points_per_million_input_tokens) if payload.chat_points_per_million_input_tokens is not None else None,
        chat_points_per_million_output_tokens=point_amount(payload.chat_points_per_million_output_tokens) if payload.chat_points_per_million_output_tokens is not None else None,
        is_active=payload.is_active,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    invalidate_ai_config_cache()
    return success({"config": ai_config_data(item), "warning": message}, "AI 模型配置已创建")


@router.put("/ai-configs/{config_id}")
def update_ai_config(config_id: int, payload: AiConfigWrite, db: Session = Depends(get_db)):
    item = db.get(AiModelConfig, config_id)
    if not item:
        raise AppException("模型配置不存在", 404)
    ok, message = _validate_model_ref(payload.model_ref)
    if not ok:
        raise AppException(message, 400)
    if payload.is_active:
        db.query(AiModelConfig).filter(AiModelConfig.id != item.id).update({AiModelConfig.is_active: False})
    _policy_payload(item, payload)
    db.add(item)
    db.commit()
    db.refresh(item)
    invalidate_ai_config_cache()
    return success({"config": ai_config_data(item), "warning": message}, "AI 模型配置已更新")


@router.delete("/ai-configs/{config_id}")
def delete_ai_config(config_id: int, db: Session = Depends(get_db)):
    item = db.get(AiModelConfig, config_id)
    if not item:
        raise AppException("模型配置不存在", 404)
    if item.is_active:
        raise AppException("不能删除正在使用的模型配置", 409)
    db.delete(item)
    db.commit()
    invalidate_ai_config_cache()
    return success(True, "AI 模型配置已删除")


# ---------------------------------------------------------------------------
# Model catalog proxy (ticket 11, ADR-0005): the AI service owns models.json;
# these routes relay admin CRUD to it. The frontend never reaches the Node
# service directly (ADR-0003). Delete protection against in-use strategies
# runs here, where the DB lives.
# ---------------------------------------------------------------------------


@router.get("/ai-catalog")
def get_ai_catalog():
    return success(call_catalog("GET", "/v1/catalog"))


@router.get("/ai-catalog/env-keys")
def get_ai_catalog_env_keys():
    """Env var NAMES (never values) usable as $VAR apiKey references, for the admin picker."""
    return success(call_catalog("GET", "/v1/catalog/env-keys"))


@router.post("/ai-catalog/providers/{provider_key}/sync-dev")
def sync_catalog_provider_dev(provider_key: str):
    """Fill blank model fields from models.dev (manual values are never overwritten)."""
    result = success(call_catalog("POST", f"/v1/catalog/providers/{provider_key}/sync-dev"))
    invalidate_catalog_capabilities_cache()
    return result


class CatalogImportModels(BaseModel):
    ids: list[str] = Field(min_length=1)


class CatalogProbe(BaseModel):
    model_id: str = Field(min_length=1, max_length=200)


@router.get("/ai-catalog/providers/{provider_key}/remote-models")
def get_catalog_remote_models(provider_key: str):
    """Upstream /models listing, read-only: lets the admin pick what to import."""
    return success(call_catalog("GET", f"/v1/catalog/providers/{provider_key}/remote-models"))


@router.post("/ai-catalog/providers/{provider_key}/import-models")
def import_catalog_models(provider_key: str, payload: CatalogImportModels):
    """Append picked upstream ids; existing models and their metadata stay untouched."""
    result = success(
        call_catalog("POST", f"/v1/catalog/providers/{provider_key}/import-models", {"ids": [i.strip() for i in payload.ids]})
    )
    invalidate_catalog_capabilities_cache()
    return result


@router.post("/ai-catalog/providers/{provider_key}/probe")
def probe_catalog_model(provider_key: str, payload: CatalogProbe):
    """One tiny completion to verify the model answers; costs a few tokens."""
    return success(
        call_catalog("POST", f"/v1/catalog/providers/{provider_key}/probe", {"model_id": payload.model_id.strip()})
    )


@router.get("/ai-catalog/providers/{provider_key}/balance")
def get_catalog_provider_balance(provider_key: str):
    """Key-level balance (60s cache in the AI service; stale value on refresh failure)."""
    return success(call_catalog("GET", f"/v1/catalog/providers/{provider_key}/balance"))


@router.get("/ai-catalog/providers/{provider_key}/plan-usage")
def get_catalog_provider_plan_usage(provider_key: str):
    """Vendor plan windows (Coding Plan / Token Plan); needs the account access key pair."""
    return success(call_catalog("GET", f"/v1/catalog/providers/{provider_key}/plan-usage"))


@router.post("/ai-catalog/providers")
def create_catalog_provider(payload: CatalogProviderWrite):
    body = {
        "key": payload.key.strip(),
        "name": payload.name.strip(),
        "baseUrl": payload.base_url.strip(),
        "apiKey": payload.api_key.strip(),
        "access_key_id": payload.access_key_id.strip(),
        "secret_access_key": payload.secret_access_key.strip(),
    }
    result = success(call_catalog("POST", "/v1/catalog/providers", body))
    invalidate_catalog_capabilities_cache()
    return result


@router.put("/ai-catalog/providers/{provider_key}")
def update_catalog_provider(provider_key: str, payload: CatalogProviderWrite):
    body = {
        "key": provider_key,
        "name": payload.name.strip(),
        "baseUrl": payload.base_url.strip(),
        "access_key_id": payload.access_key_id.strip(),
        "secret_access_key": payload.secret_access_key.strip(),
    }
    if payload.api_key.strip():
        body["apiKey"] = payload.api_key.strip()
    result = success(call_catalog("PUT", f"/v1/catalog/providers/{provider_key}", body))
    invalidate_catalog_capabilities_cache()
    return result


@router.delete("/ai-catalog/providers/{provider_key}")
def delete_catalog_provider(provider_key: str, db: Session = Depends(get_db)):
    _assert_provider_models_unreferenced(db, provider_key)
    result = success(call_catalog("DELETE", f"/v1/catalog/providers/{provider_key}"))
    invalidate_catalog_capabilities_cache()
    return result


def _catalog_ref_in_use(db: Session, ref: str) -> AiModelConfig | None:
    return db.scalar(select(AiModelConfig).where(AiModelConfig.model_ref == ref).limit(1))


def _assert_provider_models_unreferenced(db: Session, provider_key: str) -> None:
    catalog = call_catalog("GET", "/v1/catalog")
    for provider in catalog.get("providers") or []:
        if provider.get("key") != provider_key:
            continue
        for model in provider.get("models") or []:
            ref = str(model.get("ref") or "")
            if _catalog_ref_in_use(db, ref):
                raise AppException(f"模型 {ref} 正被模型策略使用，请先在模型策略中改选", 409)
    return


@router.post("/ai-catalog/providers/{provider_key}/models")
def create_catalog_model(provider_key: str, payload: CatalogModelWrite, db: Session = Depends(get_db)):
    body = {
        "id": payload.id.strip(),
        "name": payload.name.strip(),
        "reasoning": payload.reasoning,
        "input": payload.input or ["text"],
        "contextWindow": payload.context_window,
        "maxTokens": payload.max_tokens,
    }
    body = {key: value for key, value in body.items() if value is not None}
    result = success(call_catalog("POST", f"/v1/catalog/providers/{provider_key}/models", body))
    invalidate_catalog_capabilities_cache()
    return result


@router.put("/ai-catalog/providers/{provider_key}/models/{model_id}")
def update_catalog_model(provider_key: str, model_id: str, payload: CatalogModelPatch, db: Session = Depends(get_db)):
    body = {
        "name": payload.name,
        "reasoning": payload.reasoning,
        "input": payload.input,
        "contextWindow": payload.context_window,
        "maxTokens": payload.max_tokens,
    }
    body = {key: value for key, value in body.items() if value is not None}
    result = success(call_catalog("PUT", f"/v1/catalog/providers/{provider_key}/models/{model_id}", body))
    invalidate_catalog_capabilities_cache()
    return result


@router.delete("/ai-catalog/providers/{provider_key}/models/{model_id}")
def delete_catalog_model(provider_key: str, model_id: str, db: Session = Depends(get_db)):
    ref = f"{provider_key}/{model_id}"
    if _catalog_ref_in_use(db, ref):
        raise AppException(f"模型 {ref} 正被模型策略使用，请先在模型策略中改选", 409)
    result = success(call_catalog("DELETE", f"/v1/catalog/providers/{provider_key}/models/{model_id}"))
    invalidate_catalog_capabilities_cache()
    return result
