from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.models.ai_task import AiTask
from app.models.ai_config import AiModelConfig


OTHER_MODEL_FILTER_VALUE = "__other__"


def _configured_ai_model_names(db: Session) -> list[str]:
    names = {
        str(model_ref or name).strip()
        for model_ref, name in db.execute(select(AiModelConfig.model_ref, AiModelConfig.name)).all()
        if str(model_ref or name or "").strip()
    }
    return sorted(names)


def _ai_task_model_filter(db: Session, model: str | None):
    if not model:
        return None
    if model == OTHER_MODEL_FILTER_VALUE:
        configured_models = _configured_ai_model_names(db)
        other_condition = or_(AiTask.model_name.is_(None), AiTask.model_name == "")
        if configured_models:
            other_condition = or_(other_condition, AiTask.model_name.not_in(configured_models))
        return other_condition
    return AiTask.model_name == model


def _ai_model_config_lookups(
    db: Session,
) -> tuple[dict[int, AiModelConfig], dict[str, AiModelConfig]]:
    configs = list(db.scalars(select(AiModelConfig)))
    by_id = {item.id: item for item in configs}
    model_candidates: dict[str, list[AiModelConfig]] = {}
    for item in configs:
        raw_name = str(getattr(item, "model_ref", None) or "").strip()
        if raw_name:
            model_candidates.setdefault(raw_name, []).append(item)
    by_unique_model = {
        raw_name: items[0]
        for raw_name, items in model_candidates.items()
        if len(items) == 1
    }
    return by_id, by_unique_model


def admin_task_model_info(
    task: AiTask | None,
    configs_by_id: dict[int, AiModelConfig] | None = None,
    configs_by_model: dict[str, AiModelConfig] | None = None,
) -> dict[str, str | None]:
    if not task:
        return {"model_name": None, "model_config_name": None, "model_raw_name": None}
    input_data = task.input_data or {}
    config_name = input_data.get("model_name")
    raw_name = input_data.get("model") or task.model_name
    config = None
    config_id = input_data.get("model_config_id")
    if configs_by_id and config_id is not None:
        try:
            config = configs_by_id.get(int(config_id))
        except (TypeError, ValueError):
            config = None
    if not config and configs_by_model and raw_name:
        config = configs_by_model.get(str(raw_name).strip())
    if not config_name and config:
        config_name = config.name
    if not raw_name and config:
        raw_name = getattr(config, "model_ref", None)
    display_name = config_name or raw_name
    return {
        "model_name": str(display_name) if display_name else None,
        "model_config_name": str(config_name) if config_name else None,
        "model_raw_name": str(raw_name) if raw_name else None,
    }


def page_data(items: list[dict], total: int, page: int, page_size: int) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size}
