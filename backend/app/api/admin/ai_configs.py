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
from app.models.ai_config import AiModelConfig, FlowPointRule
from app.services.ai_config_service import invalidate_ai_config_cache, invalidate_catalog_capabilities_cache
from app.services.ai_service_client import call_catalog
from app.services.flow_points_service import point_amount, point_number
from app.services.model_pricing import suggest_prices


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


class CatalogModelPatchWrite(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    data: dict


class CatalogModelDelta(BaseModel):
    """模型勾选与资料随服务商一次提交：AI 服务一次写盘，不会「服务商建好了模型没进来」。"""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    patches: list[CatalogModelPatchWrite] = Field(default_factory=list)

    def to_catalog_body(self) -> dict:
        """字段名转成 AI 服务那份（camelCase），与单条模型接口同一套映射。"""
        return {
            "added": [item.strip() for item in self.added],
            "removed": [item.strip() for item in self.removed],
            "patches": [
                {"id": patch.id.strip(), "data": _catalog_model_fields(patch.data)}
                for patch in self.patches
            ],
        }


# 前端写模型用 snake_case，AI 服务读 camelCase；单条模型接口一直由这里翻译，
# 随服务商一起提交的 patches 也必须走同一套，否则 contextWindow/maxTokens 会被丢掉。
_CATALOG_MODEL_FIELDS = {
    "name": "name",
    "reasoning": "reasoning",
    "input": "input",
    "context_window": "contextWindow",
    "max_tokens": "maxTokens",
}


def _catalog_model_fields(data: dict) -> dict:
    return {
        target: data[source]
        for source, target in _CATALOG_MODEL_FIELDS.items()
        if data.get(source) is not None
    }


class CatalogProviderWrite(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    access_key_id: str = Field(default="", max_length=500)
    secret_access_key: str = Field(default="", max_length=1000)
    models: Optional[CatalogModelDelta] = None


class CatalogProviderUpdate(BaseModel):
    """更新服务商：标识来自路径，不要求客户端回传（旧前端因此一直 422）。"""

    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    access_key_id: str = Field(default="", max_length=500)
    secret_access_key: str = Field(default="", max_length=1000)
    models: Optional[CatalogModelDelta] = None


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
    AI service is reachable, warn instead of blocking when it is not.
    Routing groups ("group/<id>") are validated against the catalog's group
    list rather than the provider models."""
    ref = (model_ref or "").strip()
    if not ref:
        return True, "未选择目录模型，保存后该配置不会参与对话"
    try:
        catalog = call_catalog("GET", "/v1/catalog")
    except Exception:
        return True, "AI 服务暂不可达，未能校验模型引用；运行时若模型不存在对话会报错"
    if ref.startswith("group/"):
        groups = catalog.get("routing_groups") or []
        if any(group.get("id") == ref[len("group/"):] for group in groups):
            return True, ""
        return False, f"路由组 {ref} 不在 AI 服务目录中，请先在模型目录中创建"
    for provider in catalog.get("providers") or []:
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
# 模型定价表：目录里的每个模型 / 路由组自动成行，运营只填「一次起扣 / 输入 /
# 输出」三格加两个开关。不再有「新建策略 → 选目录模型 → 起名 → 设排序」这一套，
# 也不需要去「模型策略」里再选一次要上架的模型（那正是两层概念混淆的来源）。
# ---------------------------------------------------------------------------


def _config_brief(item: AiModelConfig | None) -> Optional[dict]:
    if item is None:
        return None
    return {
        "id": item.id,
        "points_per_call": point_number(item.chat_points_per_call) if item.chat_points_per_call is not None else None,
        "points_per_million_input_tokens": point_number(item.chat_points_per_million_input_tokens) if item.chat_points_per_million_input_tokens is not None else None,
        "points_per_million_output_tokens": point_number(item.chat_points_per_million_output_tokens) if item.chat_points_per_million_output_tokens is not None else None,
        "is_chat_selectable": bool(item.is_chat_selectable),
        "is_active": bool(item.is_active),
        "context_messages": int(item.context_messages or 12),
        "sort_order": int(item.sort_order or 100),
    }


def _catalog_or_empty() -> tuple[dict, bool]:
    """目录取不到时也要能打开这张表（否则 AI 服务抖一下后台就进不去）。"""
    try:
        return call_catalog("GET", "/v1/catalog"), True
    except Exception:
        return {"providers": [], "routing_groups": []}, False


def _upstream_prices(price_input, price_output) -> Optional[dict]:
    if price_input is None and price_output is None:
        return None
    return {"input": price_input, "output": price_output}


def _pricing_payload(db: Session) -> dict:
    catalog, catalog_ok = _catalog_or_empty()
    configs = [
        item
        for item in db.scalars(select(AiModelConfig))
        if (item.model_ref or "").strip()
    ]
    by_ref = {str(item.model_ref).strip(): item for item in configs}

    providers = catalog.get("providers") or []
    groups = catalog.get("routing_groups") or []
    model_prices: dict[str, dict] = {}
    for provider in providers:
        for model in provider.get("models") or []:
            ref = str(model.get("ref") or "").strip()
            if ref:
                model_prices[ref] = _upstream_prices(model.get("price_input"), model.get("price_output"))

    items: list[dict] = []
    seen_refs: set[str] = set()

    for provider in providers:
        for model in provider.get("models") or []:
            ref = str(model.get("ref") or "").strip()
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            prices = model_prices.get(ref)
            items.append(
                {
                    "ref": ref,
                    "name": str(model.get("name") or model.get("id") or ref),
                    "kind": "model",
                    "provider_key": str(provider.get("key") or ""),
                    "provider_name": str(provider.get("name") or provider.get("key") or ""),
                    "multimodal": "image" in (model.get("input") or []),
                    "context_window": model.get("context_window"),
                    "member_count": 0,
                    "missing": False,
                    "upstream": prices,
                    "suggested": suggest_prices(
                        (prices or {}).get("input"), (prices or {}).get("output")
                    ),
                    "config": _config_brief(by_ref.pop(ref, None)),
                }
            )

    for group in groups:
        group_id = str(group.get("id") or "").strip()
        ref = f"group/{group_id}"
        if not group_id or ref in seen_refs:
            continue
        seen_refs.add(ref)
        members = [str(member).strip() for member in group.get("members") or [] if str(member).strip()]
        member_prices = [model_prices[member] for member in members if model_prices.get(member)]
        # 组内任意成员都可能接这一轮请求，按最贵的那个定价，避免路由到旗舰时亏钱
        inputs = [p["input"] for p in member_prices if p.get("input") is not None]
        outputs = [p["output"] for p in member_prices if p.get("output") is not None]
        prices = _upstream_prices(max(inputs) if inputs else None, max(outputs) if outputs else None)
        items.append(
            {
                "ref": ref,
                "name": f"路由组 {group_id}",
                "kind": "group",
                "provider_key": None,
                "provider_name": "路由组",
                "multimodal": False,
                "context_window": None,
                "member_count": len(members),
                "missing": False,
                "upstream": prices,
                "suggested": suggest_prices((prices or {}).get("input"), (prices or {}).get("output")),
                "config": _config_brief(by_ref.pop(ref, None)),
            }
        )

    # 还有定价行、但模型已经从目录里消失（换提供商 / 取消勾选）：留着让运营自己决定
    # 是补回来还是移除，不要静默消失。
    for ref, item in by_ref.items():
        items.append(
            {
                "ref": ref,
                "name": item.name,
                "kind": "group" if ref.startswith("group/") else "model",
                "provider_key": ref.split("/", 1)[0],
                "provider_name": "",
                "multimodal": False,
                "context_window": None,
                "member_count": 0,
                "missing": True,
                "upstream": None,
                "suggested": None,
                "config": _config_brief(item),
            }
        )

    def _rank(entry: dict) -> tuple[int, int]:
        config = entry.get("config") or {}
        return (0 if config.get("is_active") else 1, 0 if config.get("is_chat_selectable") else 1)

    # 顺序只在保存后按「默认 → 已上架 → 其余」重排，编辑过程中不改行序
    items.sort(key=_rank)

    rule = db.scalar(select(FlowPointRule).where(FlowPointRule.feature_type == "ai_chat"))
    return {
        "items": items,
        "defaults": {
            "points_per_call": point_number(rule.points_per_call) if rule else None,
            "points_per_million_input_tokens": point_number(rule.points_per_million_input_tokens) if rule else None,
            "points_per_million_output_tokens": point_number(rule.points_per_million_output_tokens) if rule else None,
        },
        "catalog_available": catalog_ok,
        "unbound_configs": len(
            [item for item in db.scalars(select(AiModelConfig)) if not (item.model_ref or "").strip()]
        ),
    }


@router.get("/model-pricing")
def get_model_pricing(db: Session = Depends(get_db)):
    return success(_pricing_payload(db))


class ModelPricingWriteItem(BaseModel):
    ref: str = Field(min_length=1, max_length=200)
    name: Optional[str] = Field(default=None, max_length=200)
    points_per_call: Optional[Decimal] = Field(default=None, ge=0)
    points_per_million_input_tokens: Optional[Decimal] = Field(default=None, ge=0)
    points_per_million_output_tokens: Optional[Decimal] = Field(default=None, ge=0)
    is_chat_selectable: bool = False
    is_active: bool = False
    context_messages: int = Field(default=12, ge=1, le=40)
    sort_order: int = Field(default=100, ge=0, le=9999)


class ModelPricingWrite(BaseModel):
    items: list[ModelPricingWriteItem] = Field(default_factory=list)


@router.put("/model-pricing")
def save_model_pricing(payload: ModelPricingWrite, db: Session = Depends(get_db)):
    """整表覆盖：表里有什么就是什么，没出现的目录模型视为未上架（删掉定价行）。"""
    items = payload.items
    refs = [item.ref.strip() for item in items]
    if len(refs) != len(set(refs)):
        raise AppException("同一个模型出现了两行定价，请刷新后重试", 400)
    defaults = [item for item in items if item.is_active]
    if len(defaults) > 1:
        raise AppException("只能有一个默认模型", 400)
    if items and not defaults:
        raise AppException("请指定一个默认模型：用户没有手选模型时按它发起对话", 400)
    if not items:
        catalog, _ = _catalog_or_empty()
        if any(provider.get("models") for provider in catalog.get("providers") or []):
            raise AppException("至少要上架一个模型，否则对话没有可用的默认模型", 400)

    existing = {
        str(item.model_ref).strip(): item
        for item in db.scalars(select(AiModelConfig))
        if (item.model_ref or "").strip()
    }
    keep = {item.ref.strip() for item in items}
    for ref, item in list(existing.items()):
        if ref not in keep:
            db.delete(item)
            existing.pop(ref)

    for entry in items:
        ref = entry.ref.strip()
        item = existing.get(ref)
        if item is None:
            item = AiModelConfig(model_ref=ref, name=(entry.name or ref)[:80])
            db.add(item)
            existing[ref] = item
        elif entry.name:
            item.name = entry.name[:80]
        # 默认模型必须能被用户选中，否则「默认」在人眼里就消失了
        item.is_chat_selectable = bool(entry.is_chat_selectable or entry.is_active)
        item.is_active = bool(entry.is_active)
        item.context_messages = entry.context_messages
        item.sort_order = entry.sort_order
        item.chat_points_per_call = point_amount(entry.points_per_call) if entry.points_per_call is not None else None
        item.chat_points_per_million_input_tokens = point_amount(entry.points_per_million_input_tokens) if entry.points_per_million_input_tokens is not None else None
        item.chat_points_per_million_output_tokens = point_amount(entry.points_per_million_output_tokens) if entry.points_per_million_output_tokens is not None else None

    # 老「模型策略」可能留下没绑定目录模型的行（model_ref 为空）：它们跑不了对话，
    # 也不属于这张表，顺手清掉，避免默认模型被一条空行占着。
    for item in db.scalars(select(AiModelConfig)):
        if not (item.model_ref or "").strip():
            db.delete(item)

    db.commit()
    invalidate_ai_config_cache()
    invalidate_catalog_capabilities_cache()
    return success(_pricing_payload(db), "模型定价已保存")


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


class CatalogRemoteModelsProbe(BaseModel):
    """读取上游模型列表用的表单值：不落盘，新建服务商也能先拉一次。"""

    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=1000)
    provider_key: str = Field(default="", max_length=120)


@router.post("/ai-catalog/remote-models")
def post_catalog_remote_models(payload: CatalogRemoteModelsProbe):
    """Same listing from the card as typed; blank fields fall back to the saved provider."""
    return success(
        call_catalog(
            "POST",
            "/v1/catalog/remote-models",
            {"base_url": payload.base_url.strip(), "api_key": payload.api_key.strip(), "provider_key": payload.provider_key.strip()},
        )
    )


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


# Routing groups: one ref that fans out to several catalog models so a dead
# provider fails over silently (ai-service router.ts). A group referenced by a
# model config cannot be deleted — that protection lives here, where the DB is.
@router.post("/ai-catalog/groups")
def create_catalog_group(payload: dict):
    result = success(call_catalog("POST", "/v1/catalog/groups", payload))
    invalidate_catalog_capabilities_cache()
    return result


@router.put("/ai-catalog/groups/{group_id}")
def update_catalog_group(group_id: str, payload: dict):
    result = success(call_catalog("PUT", f"/v1/catalog/groups/{group_id}", payload))
    invalidate_catalog_capabilities_cache()
    return result


@router.delete("/ai-catalog/groups/{group_id}")
def delete_catalog_group(group_id: str, db: Session = Depends(get_db)):
    in_use = (
        db.query(AiModelConfig)
        .filter(AiModelConfig.model_ref == f"group/{group_id}")
        .first()
    )
    if in_use:
        raise AppException(f"路由组 group/{group_id} 正被模型定价里的「{in_use.name}」使用，请先解除绑定", 409)
    result = success(call_catalog("DELETE", f"/v1/catalog/groups/{group_id}"))
    invalidate_catalog_capabilities_cache()
    return result


@router.post("/ai-catalog/providers")
def create_catalog_provider(payload: CatalogProviderWrite):
    body = {
        "key": payload.key.strip(),
        "name": payload.name.strip(),
        "baseUrl": payload.base_url.strip(),
        "apiKey": payload.api_key.strip(),
        "access_key_id": payload.access_key_id.strip(),
        "secret_access_key": payload.secret_access_key.strip(),
        "models": payload.models.to_catalog_body() if payload.models else None,
    }
    result = success(call_catalog("POST", "/v1/catalog/providers", body))
    invalidate_catalog_capabilities_cache()
    return result


@router.put("/ai-catalog/providers/{provider_key}")
def update_catalog_provider(provider_key: str, payload: CatalogProviderUpdate, db: Session = Depends(get_db)):
    # 取消勾选即删除：模型被模型策略引用时先停下，与单条删除接口同一判据
    for model_id in (payload.models.removed if payload.models else []):
        ref = f"{provider_key}/{model_id}"
        if _catalog_ref_in_use(db, ref):
            raise AppException(f"模型 {ref} 正被模型定价使用，请先在「模型定价」里改掉", 409)
    body = {
        "key": provider_key,
        "name": payload.name.strip(),
        "baseUrl": payload.base_url.strip(),
        "access_key_id": payload.access_key_id.strip(),
        "secret_access_key": payload.secret_access_key.strip(),
        "models": payload.models.to_catalog_body() if payload.models else None,
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
                raise AppException(f"模型 {ref} 正被模型定价使用，请先在「模型定价」里改掉", 409)
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
        raise AppException(f"模型 {ref} 正被模型定价使用，请先在「模型定价」里改掉", 409)
    result = success(call_catalog("DELETE", f"/v1/catalog/providers/{provider_key}/models/{model_id}"))
    invalidate_catalog_capabilities_cache()
    return result
