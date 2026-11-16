from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

import httpx

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import AppException
from app.core.redis import redis_client
from app.models.ai_config import AiModelConfig, FlowPointRule
from app.services.ai_service_client import afetch_catalog_payload


logger = logging.getLogger(__name__)


AI_CONFIG_CACHE_KEY = f"{settings.redis_key_prefix}:ai:active-model-config:v2"
AI_CONFIG_CACHE_SECONDS = 300
CATALOG_CAPABILITIES_CACHE_KEY = f"{settings.redis_key_prefix}:ai:catalog-capabilities"
CATALOG_CAPABILITIES_CACHE_SECONDS = 60


@dataclass
class AiRuntimeConfig:
    id: int | None
    name: str
    model_ref: str | None
    context_messages: int
    is_chat_selectable: bool = True
    sort_order: int = 100
    chat_points_per_call: float | None = None
    chat_points_per_million_input_tokens: float | None = None
    chat_points_per_million_output_tokens: float | None = None

    def chat_billing_override(self) -> dict[str, float | None]:
        return {
            "points_per_call": self.chat_points_per_call,
            "points_per_million_input_tokens": self.chat_points_per_million_input_tokens,
            "points_per_million_output_tokens": self.chat_points_per_million_output_tokens,
        }


def _decimal_number(value: Decimal | int | float | str | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _from_model(item: AiModelConfig) -> AiRuntimeConfig:
    return AiRuntimeConfig(
        id=item.id,
        name=item.name,
        model_ref=getattr(item, "model_ref", None) or None,
        context_messages=max(1, min(int(item.context_messages or 12), 40)),
        is_chat_selectable=bool(getattr(item, "is_chat_selectable", True)),
        sort_order=int(getattr(item, "sort_order", 100) or 100),
        chat_points_per_call=_decimal_number(getattr(item, "chat_points_per_call", None)),
        chat_points_per_million_input_tokens=_decimal_number(getattr(item, "chat_points_per_million_input_tokens", None)),
        chat_points_per_million_output_tokens=_decimal_number(getattr(item, "chat_points_per_million_output_tokens", None)),
    )


def _cache_config(config: AiRuntimeConfig) -> None:
    try:
        redis_client.set(
            AI_CONFIG_CACHE_KEY,
            json.dumps(asdict(config), ensure_ascii=False),
            ex=AI_CONFIG_CACHE_SECONDS,
        )
    except RedisError:
        logger.warning("Failed to write AI config cache", exc_info=True)


def _read_cached_config() -> AiRuntimeConfig | None:
    try:
        value = redis_client.get(AI_CONFIG_CACHE_KEY)
        if not value:
            return None
        data = json.loads(value)
        return AiRuntimeConfig(**data)
    except (RedisError, json.JSONDecodeError, TypeError):
        logger.warning("Failed to read AI config cache", exc_info=True)
        return None


def invalidate_ai_config_cache() -> None:
    try:
        redis_client.delete(AI_CONFIG_CACHE_KEY)
    except RedisError:
        logger.warning("Failed to invalidate AI config cache", exc_info=True)


def invalidate_catalog_capabilities_cache() -> None:
    try:
        redis_client.delete(CATALOG_CAPABILITIES_CACHE_KEY)
    except RedisError:
        logger.warning("Failed to invalidate catalog capabilities cache", exc_info=True)


def _select_active_config(session: Session) -> AiModelConfig | None:
    return session.scalar(
        select(AiModelConfig)
        .where(AiModelConfig.is_active == True)  # noqa: E712
        .order_by(AiModelConfig.sort_order.asc(), AiModelConfig.update_time.desc(), AiModelConfig.id.desc())
        .limit(1)
    )


def get_ai_config_by_id(db: Session, config_id: int, *, require_chat_selectable: bool = False) -> AiRuntimeConfig:
    item = db.get(AiModelConfig, config_id)
    if not item:
        raise AppException("模型配置不存在或已停用", 404)
    if require_chat_selectable and not bool(getattr(item, "is_chat_selectable", True)):
        raise AppException("该模型暂未开放给 AI 助手使用", 403)
    return _require_model_ref(_from_model(item))


def _require_model_ref(config: AiRuntimeConfig) -> AiRuntimeConfig:
    if not config.model_ref:
        raise AppException("该模型配置尚未选择目录模型，请在管理后台重新选择")
    return config


def get_default_chat_ai_config(db: Session, *, require_model_ref: bool = True) -> AiRuntimeConfig:
    item = db.scalar(
        select(AiModelConfig)
        .where(AiModelConfig.is_chat_selectable == True, AiModelConfig.is_active == True)  # noqa: E712
        .order_by(AiModelConfig.sort_order.asc(), AiModelConfig.update_time.desc(), AiModelConfig.id.desc())
        .limit(1)
    )
    if not item:
        item = db.scalar(
            select(AiModelConfig)
            .where(AiModelConfig.is_chat_selectable == True)  # noqa: E712
            .order_by(AiModelConfig.sort_order.asc(), AiModelConfig.update_time.desc(), AiModelConfig.id.desc())
            .limit(1)
        )
    config = _from_model(item) if item else get_active_ai_config(db)
    return _require_model_ref(config) if require_model_ref else config


def resolve_chat_ai_config(
    db: Session,
    user: Any,
    model_config_id: int | None = None,
) -> AiRuntimeConfig:
    """Resolve the chat model config for one request (ticket 07).

    Mirrors the legacy ``new_task`` chat selection: an explicitly requested
    chat-selectable model wins (admins may pick any model), otherwise the
    default chat model. The result drives the multimodal guard, the pi model
    mapping (``model_ref``) and the chat billing override.
    """
    is_admin = getattr(user, "role", "") == "admin"
    if model_config_id:
        return get_ai_config_by_id(db, int(model_config_id), require_chat_selectable=not is_admin)
    return get_default_chat_ai_config(db)


def get_ai_config_by_model_ref(db: Session, model_ref: str) -> AiRuntimeConfig | None:
    """Find the model config mapped to a catalog model ref (billing granularity).

    The Node AI service reports usage as ``"provider/model"``; the matching
    config supplies the per-model chat rates. Unmapped refs return None and
    the caller falls back to the default ai_chat rule instead of losing the
    charge.

    这里**不能**再过滤 ``is_active``：``is_active`` 的语义是「默认对话模型」，
    全局只有一行；以前按它过滤，等于只有默认模型的自定义价格生效，用户在
    选择器里选的其他模型一律按全局兜底价扣——运营改完价账单不变，原因就在这。
    一个 ref 现在最多一行（0041 唯一索引），直接取即可。
    """
    ref = (model_ref or "").strip()
    if not ref:
        return None
    item = db.scalar(select(AiModelConfig).where(AiModelConfig.model_ref == ref).limit(1))
    return _from_model(item) if item else None


def get_active_ai_config(db: Session | None = None) -> AiRuntimeConfig:
    cached = _read_cached_config()
    if cached:
        return cached

    owns_session = db is None
    session = db or SessionLocal()
    try:
        item = _select_active_config(session)
        if not item:
            raise AppException("请先在管理后台配置 AI 模型")
        config = _from_model(item)
        _cache_config(config)
        return config
    finally:
        if owns_session:
            session.close()


def _capabilities_from_providers(providers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    capabilities: dict[str, dict[str, Any]] = {}
    for provider in providers:
        for model in provider.get("models") or []:
            ref = str(model.get("ref") or "").strip()
            if ref:
                capabilities[ref] = {
                    "multimodal": "image" in (model.get("input") or []),
                    "context_window": model.get("context_window"),
                }
    return capabilities


def _capabilities_from_groups(
    capabilities: dict[str, dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Derive "group/<id>" capability entries from their members.

    A group is multimodal only if every known member is (an unknown member —
    e.g. catalog unreachable at cache time — is skipped rather than vetoing);
    the usable context window is the smallest member's, since any turn may
    land on the weakest member."""
    for group in groups:
        ref = f"group/{group.get('id', '')}".strip()
        members = [str(m or "").strip() for m in group.get("members") or [] if str(m or "").strip()]
        member_entries = [capabilities[m] for m in members if m in capabilities]
        if not member_entries:
            continue
        capabilities[ref] = {
            "multimodal": all(entry.get("multimodal") for entry in member_entries),
            "context_window": min(
                (entry.get("context_window") for entry in member_entries if entry.get("context_window")),
                default=None,
            ),
        }
    return capabilities


def _read_capabilities_cache() -> dict[str, dict[str, Any]] | None:
    try:
        cached = redis_client.get(CATALOG_CAPABILITIES_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except (RedisError, json.JSONDecodeError, TypeError):
        logger.warning("Failed to read catalog capabilities cache", exc_info=True)
    return None


async def get_catalog_capabilities() -> dict[str, dict[str, Any]]:
    """Map of model ref -> capability flags from the AI service catalog.

    Backs the derived ``supports_multimodal`` (ADR-0005): the catalog's
    ``input`` array is the authority. Cached in Redis for 60s; a failed fetch
    returns an empty map so callers fall back to permissive behavior instead of
    hard-failing chat because the catalog is briefly unreachable.
    """
    cached = _read_capabilities_cache()
    if cached is not None:
        return cached
    try:
        payload = await afetch_catalog_payload()
    except Exception:
        return {}
    capabilities = _capabilities_from_groups(
        _capabilities_from_providers(payload.get("providers") or []),
        payload.get("routing_groups") or [],
    )
    _cache_capabilities(capabilities)
    return capabilities


def get_catalog_capabilities_sync() -> dict[str, dict[str, Any]]:
    """Sync twin of :func:`get_catalog_capabilities` for sync routes (new_task)."""
    cached = _read_capabilities_cache()
    if cached is not None:
        return cached
    try:
        from app.services.ai_service_client import _catalog_headers, _catalog_url

        with httpx.Client(timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5)) as client:
            resp = client.get(_catalog_url("/v1/catalog"), headers=_catalog_headers())
        payload = resp.json() if resp.status_code < 400 else {}
    except Exception:
        return {}
    capabilities = _capabilities_from_groups(
        _capabilities_from_providers(payload.get("providers") or []),
        payload.get("routing_groups") or [],
    )
    _cache_capabilities(capabilities)
    return capabilities


def _cache_capabilities(capabilities: dict[str, dict[str, Any]]) -> None:
    try:
        redis_client.set(
            CATALOG_CAPABILITIES_CACHE_KEY,
            json.dumps(capabilities, ensure_ascii=False),
            ex=CATALOG_CAPABILITIES_CACHE_SECONDS,
        )
    except RedisError:
        logger.warning("Failed to write catalog capabilities cache", exc_info=True)


def capability_supports_images(capabilities: dict[str, dict[str, Any]], model_ref: str | None) -> bool:
    """Whether a model ref accepts images; unknown refs stay permissive."""
    entry = capabilities.get((model_ref or "").strip())
    if entry is None:
        return True
    return bool(entry.get("multimodal"))


async def public_ai_capability(db: Session, *, include_all_chat_models: bool = False) -> dict[str, Any]:
    config = get_active_ai_config(db)
    # 策略允许暂未选择目录模型（保存时仅告警）：能力接口要照常返回，
    # 让前端能渲染模型列表；真正发起对话时才校验 model_ref。
    default_chat_config = get_default_chat_ai_config(db, require_model_ref=False)
    capabilities = await get_catalog_capabilities()
    chat_rule = db.scalar(select(FlowPointRule).where(FlowPointRule.feature_type == "ai_chat"))
    default_points_per_call = _decimal_number(getattr(chat_rule, "points_per_call", None))
    default_input_rate = _decimal_number(getattr(chat_rule, "points_per_million_input_tokens", None))
    default_output_rate = _decimal_number(getattr(chat_rule, "points_per_million_output_tokens", None))
    model_query = select(AiModelConfig)
    if not include_all_chat_models:
        model_query = model_query.where(AiModelConfig.is_chat_selectable == True)  # noqa: E712
    items = list(
        db.scalars(
            model_query.order_by(
                AiModelConfig.sort_order.asc(),
                AiModelConfig.is_active.desc(),
                AiModelConfig.update_time.desc(),
                AiModelConfig.id.desc(),
            )
        )
    )
    if not items:
        active_item = _select_active_config(db)
        if active_item:
            items = [active_item]
    return {
        "id": config.id,
        "name": config.name,
        "supports_multimodal": capability_supports_images(capabilities, config.model_ref),
        "context_messages": config.context_messages,
        "default_chat_model_id": default_chat_config.id,
        "chat_models": [
            {
                "id": item.id,
                "name": item.name,
                "supports_multimodal": capability_supports_images(capabilities, getattr(item, "model_ref", None)),
                "context_messages": max(1, min(int(item.context_messages or 12), 40)),
                "sort_order": int(getattr(item, "sort_order", 100) or 100),
                "points_per_call": (
                    _decimal_number(item.chat_points_per_call)
                    if item.chat_points_per_call is not None
                    else default_points_per_call
                ),
                "points_per_million_input_tokens": (
                    _decimal_number(item.chat_points_per_million_input_tokens)
                    if item.chat_points_per_million_input_tokens is not None
                    else default_input_rate
                ),
                "points_per_million_output_tokens": (
                    _decimal_number(item.chat_points_per_million_output_tokens)
                    if item.chat_points_per_million_output_tokens is not None
                    else default_output_rate
                ),
                "uses_default_pricing": (
                    item.chat_points_per_call is None
                    and item.chat_points_per_million_input_tokens is None
                    and item.chat_points_per_million_output_tokens is None
                ),
            }
            for item in items
        ],
    }
