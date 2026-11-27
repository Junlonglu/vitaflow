"""原子信息库：把简历拆成可复用的信息条目。

设计要点：

* **一条信息 = 一个模块条目**。``payload`` 直接使用简历 schema 的标准字段，
  导入简历时不需要再翻译一次；分类只决定落到哪个模块。
* **判重靠指纹**。同分类 + 同内容判为同一条，重复提取 / 重复导入不会造出
  一模一样的第二条。
* **导入不覆盖**。把条目写进简历时按「追加」处理，已存在的同内容条目跳过；
  需要改写用户已有的经历时仍走对话里的待确认修改，不在这里静默替换。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.resume_schema import (
    FRAGMENT_KINDS,
    FRAGMENT_KIND_ORDER,
    SECTIONS,
    fragment_kind,
    fragment_summary,
    guess_fragment_kind,
    fragment_title,
    normalize_fragment_payload,
    normalize_resume_document,
)
from app.models.info_fragment import InfoFragment
from app.models.resume import Resume
from app.services.resume_placeholder import is_blank, is_placeholder_value

MAX_FRAGMENTS_PER_USER = 500
MAX_BULK_CREATE = 30
MAX_TAGS = 8
MAX_TAG_LENGTH = 20
MAX_EXTRACT_ITEMS = 120
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# 简历模块 → 原子信息分类（反向映射，用于「从简历提取」）
SECTION_TO_KIND = {
    "education": "education",
    "work": "work",
    "projects": "project",
    "skills": "skill",
    "awards": "award",
}
PRESET_TO_KIND = {
    "internships": "internship",
    "certifications": "certificate",
    "languages": "language",
    "training": "training",
    "competitions": "competition",
    "campus": "campus",
    "publications": "publication",
    "social_practice": "volunteer",
}
CUSTOM_SECTION_TITLES = {
    "internship": "实习经历",
    "certificate": "证书/认证",
    "language": "语言能力",
    "training": "培训经历",
    "competition": "竞赛经历",
    "campus": "校园经历",
    "publication": "论文/专利",
    "volunteer": "志愿服务",
    "custom": "自定义模块",
}


def _clean_tags(tags: Iterable[Any] | None) -> list[str]:
    result: list[str] = []
    for raw in tags or []:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()[:MAX_TAG_LENGTH]
        if text and text not in result:
            result.append(text)
        if len(result) >= MAX_TAGS:
            break
    return result


def _search_text(kind: str, title: str, summary: str, tags: list[str], payload: dict[str, Any]) -> str:
    spec = fragment_kind(kind)
    label = spec.label if spec else ""
    values: list[str] = [label, title, summary, " ".join(tags)]
    for value in payload.values():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if isinstance(item, (str, int, float)))
    return re.sub(r"\s+", " ", " ".join(part for part in values if part))[:4000]


def _fingerprint(kind: str, payload: dict[str, Any]) -> str:
    if kind == "basic":
        # 个人信息按「身份」判重，不按整条 payload：title / status / expected_salary
        # 是求职意向，换方向就会变，混进指纹会让同一个人变成两条（历史数据里已经出现）。
        identity = _basic_identity_key(payload)
        if identity:
            return identity
    material = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _normalize_identity(value: Any) -> str:
    text = re.sub(r"[\s\-()（）]", "", str(value or ""))
    return text.strip().lower()


def _basic_identity_key(payload: dict[str, Any]) -> str:
    """身份指纹：姓名 + 首要联系方式（邮箱优先，其次手机号）。

    用「邮箱优先」而不是手机号：换号比换邮箱常见，按手机号判重会把同一个人
    拆成两条；两者都没有时退回姓名，姓名也没有就不做身份判重。
    """
    payload = payload if isinstance(payload, dict) else {}
    name = _normalize_identity(payload.get("name"))
    contact = _normalize_identity(payload.get("email")) or _normalize_identity(payload.get("phone"))
    if not name and not contact:
        return ""
    material = json.dumps(
        {"kind": "basic", "name": name, "contact": contact}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _build_row_values(
    kind: str,
    payload: Any,
    title: str | None = None,
    tags: Iterable[Any] | None = None,
) -> dict[str, Any]:
    spec = fragment_kind(kind)
    if spec is None:
        raise AppException(f"不支持的信息分类：{kind}", 400)
    cleaned, issue = normalize_fragment_payload(spec.kind, payload)
    if issue:
        raise AppException(issue, 400)
    clean_tags = _clean_tags(tags)
    resolved_title = (str(title or "").strip() or fragment_title(spec.kind, cleaned))[:120]
    summary = fragment_summary(spec.kind, cleaned)
    return {
        "kind": spec.kind,
        "title": resolved_title,
        "summary": summary,
        "payload": cleaned,
        "tags": clean_tags,
        "search_text": _search_text(spec.kind, resolved_title, summary, clean_tags, cleaned),
        "fingerprint": _fingerprint(spec.kind, cleaned),
    }


def fragment_kind_label(kind: str) -> str:
    spec = fragment_kind(kind)
    return spec.label if spec else str(kind or "")


def fragment_out(row: InfoFragment) -> dict[str, Any]:
    spec = fragment_kind(row.kind)
    return {
        "id": row.id,
        "kind": row.kind,
        "kind_label": spec.label if spec else row.kind,
        "title": row.title,
        "summary": row.summary,
        "payload": row.payload or {},
        "tags": row.tags or [],
        "source": row.source,
        "enabled": bool(row.enabled),
        "is_default": bool(row.is_default),
        "usage_count": row.usage_count or 0,
        "last_used_time": row.last_used_time,
        "update_time": row.update_time,
        "create_time": row.create_time,
    }


def _user_row(db: Session, user_id: int, fragment_id: int) -> InfoFragment:
    row = db.get(InfoFragment, fragment_id)
    if not row or row.user_id != user_id:
        raise AppException("信息不存在", 404)
    return row


def resolve_default_basic(db: Session, user_id: int) -> InfoFragment | None:
    """取「我」的那一条个人信息：优先显式默认，其次用得最多的那条。

    没有默认时不返回 None 就完事——历史数据（迁移前写入）没有标记，回退到
    usage_count 能保证老用户也能被自动补齐。
    """
    base = [InfoFragment.user_id == user_id, InfoFragment.kind == "basic", InfoFragment.enabled.is_(True)]
    row = db.scalar(
        select(InfoFragment)
        .where(*base, InfoFragment.is_default.is_(True))
        .order_by(InfoFragment.update_time.desc(), InfoFragment.id.desc())
        .limit(1)
    )
    if row is not None:
        return row
    return db.scalar(
        select(InfoFragment)
        .where(*base)
        .order_by(InfoFragment.usage_count.desc(), InfoFragment.update_time.desc(), InfoFragment.id.desc())
        .limit(1)
    )


def set_default_fragment(db: Session, user_id: int, fragment_id: int) -> InfoFragment:
    """把一条个人信息设为默认；同用户其它默认条目一起清掉。"""
    row = _user_row(db, user_id, fragment_id)
    if row.kind != "basic":
        raise AppException("只有个人信息可以设为默认", 400)
    db.execute(
        update(InfoFragment)
        .where(InfoFragment.user_id == user_id, InfoFragment.is_default.is_(True))
        .values(is_default=False)
        .execution_options(synchronize_session=False)
    )
    row.is_default = True
    if not row.enabled:
        row.enabled = True
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _escape_like(keyword: str) -> str:
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_fragments(
    db: Session,
    user_id: int,
    kind: str | None = None,
    keyword: str | None = None,
    tag: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[InfoFragment], int]:
    filters = [InfoFragment.user_id == user_id]
    if kind:
        filters.append(InfoFragment.kind == kind)
    text = re.sub(r"\s+", " ", str(keyword or "")).strip()[:60]
    if text:
        filters.append(InfoFragment.search_text.like(f"%{_escape_like(text)}%", escape="\\"))
    clean_tag = str(tag or "").strip()[:MAX_TAG_LENGTH]
    if clean_tag:
        # 标签量很少（每人 ≤8 个/条），用文本包含匹配即可，避免 JSON 查询的方言差异
        filters.append(or_(InfoFragment.search_text.like(f"%{_escape_like(clean_tag)}%", escape="\\")))

    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    total = db.scalar(select(func.count()).select_from(InfoFragment).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(InfoFragment)
            .where(*filters)
            .order_by(InfoFragment.update_time.desc(), InfoFragment.id.desc())
            .offset((safe_page - 1) * safe_size)
            .limit(safe_size)
        )
    )
    return rows, total


def counts_by_kind(db: Session, user_id: int) -> dict[str, int]:
    rows = db.execute(
        select(InfoFragment.kind, func.count())
        .where(InfoFragment.user_id == user_id)
        .group_by(InfoFragment.kind)
    ).all()
    counts = {kind: 0 for kind in FRAGMENT_KIND_ORDER}
    for kind, count in rows:
        counts[str(kind)] = int(count or 0)
    return counts


def kind_options() -> list[dict[str, Any]]:
    """分类清单（含字段表）：前端表单与 Agent 提示词共用同一份定义。"""
    return [
        {
            "kind": spec.kind,
            "label": spec.label,
            "section": spec.section,
            "section_label": SECTIONS[spec.section].label if spec.section in SECTIONS else spec.section,
            "description": spec.description,
            "title_field": spec.title_field,
            "preset_type": spec.preset_type,
            "fields": [
                {"key": field.key, "label": field.label, "type": field.type, "required": field.required}
                for field in spec.fields
            ],
        }
        for spec in FRAGMENT_KINDS.values()
    ]


def _find_same_identity(db: Session, user_id: int, payload: dict[str, Any]) -> InfoFragment | None:
    """按身份找已有的 basic 条目。

    历史行的指纹是「整条 payload」，与新的身份指纹对不上，所以这里补一次
    身份比对；只扫 basic（每人个位数），不做全表匹配。
    """
    key = _basic_identity_key(payload)
    if not key:
        return None
    rows = db.scalars(
        select(InfoFragment)
        .where(InfoFragment.user_id == user_id, InfoFragment.kind == "basic")
        .order_by(InfoFragment.update_time.desc())
    )
    for row in rows:
        if _basic_identity_key(row.payload or {}) == key:
            return row
    return None


def _upsert(
    db: Session,
    user_id: int,
    values: dict[str, Any],
    source: str,
    dedupe: bool = True,
) -> tuple[InfoFragment, bool]:
    existing = None
    if dedupe:
        existing = db.scalar(
            select(InfoFragment).where(
                InfoFragment.user_id == user_id,
                InfoFragment.fingerprint == values["fingerprint"],
            )
        )
        if existing is None and values["kind"] == "basic":
            existing = _find_same_identity(db, user_id, values["payload"])
    if existing is not None:
        # 同内容已存在：只在标题/标签有新增时补上去，不改用户后来编辑过的内容
        changed = False
        if values["title"] and values["title"] != existing.title and values["title"] not in (existing.title or ""):
            existing.title = values["title"]
            changed = True
        if existing.kind == "basic":
            # 身份相同但字段有新值（换手机、补城市）：只填空，不覆盖用户已存内容。
            merged_payload = dict(existing.payload or {})
            filled = False
            for key, value in (values["payload"] or {}).items():
                if is_blank(merged_payload.get(key)) and not is_blank(value):
                    merged_payload[key] = value
                    filled = True
            if filled:
                existing.payload = merged_payload
                existing.summary = fragment_summary(existing.kind, merged_payload)
                changed = True
            # 指纹收敛到身份键，下次直接走快路径。
            if values["fingerprint"] != existing.fingerprint:
                existing.fingerprint = values["fingerprint"]
                changed = True
        merged_tags = _clean_tags([*(existing.tags or []), *values["tags"]])
        if merged_tags != (existing.tags or []):
            existing.tags = merged_tags
            changed = True
        if changed:
            existing.search_text = _search_text(
                existing.kind, existing.title, existing.summary, existing.tags or [], existing.payload or {}
            )
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return existing, False

    total = db.scalar(select(func.count()).select_from(InfoFragment).where(InfoFragment.user_id == user_id)) or 0
    if total >= MAX_FRAGMENTS_PER_USER:
        raise AppException(f"信息库最多保存 {MAX_FRAGMENTS_PER_USER} 条，请先清理不需要的信息", 400)
    row = InfoFragment(user_id=user_id, source=source, enabled=True, usage_count=0, **values)
    # 第一条个人信息自动成为默认：新用户不必先去哪个页面点一次「设为默认」。
    if values["kind"] == "basic" and resolve_default_basic(db, user_id) is None:
        row.is_default = True
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, True


def create_fragment(
    db: Session,
    user_id: int,
    kind: str,
    payload: Any,
    title: str | None = None,
    tags: Iterable[Any] | None = None,
    source: str = "manual",
    dedupe: bool = True,
) -> tuple[InfoFragment, bool]:
    values = _build_row_values(kind, payload, title, tags)
    return _upsert(db, user_id, values, source, dedupe=dedupe)


def update_fragment(
    db: Session,
    user_id: int,
    fragment_id: int,
    kind: str | None = None,
    payload: Any | None = None,
    title: str | None = None,
    tags: Iterable[Any] | None = None,
) -> InfoFragment:
    row = _user_row(db, user_id, fragment_id)
    values = _build_row_values(
        kind or row.kind,
        payload if payload is not None else row.payload,
        # 没显式给标题时按新 payload 重新推导：改了公司/项目名后标题要跟着变，
        # 否则列表里还是旧名字。
        title if title is not None else None,
        tags if tags is not None else row.tags,
    )
    for key, value in values.items():
        setattr(row, key, value)
    row.source = "manual"
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_fragment(db: Session, user_id: int, fragment_id: int) -> None:
    row = _user_row(db, user_id, fragment_id)
    was_default = bool(row.is_default) and row.kind == "basic"
    db.delete(row)
    db.commit()
    if not was_default:
        return
    # 默认条目被删掉后自动接替，否则「自动补基本信息」会静默失效，用户看不出原因。
    next_row = resolve_default_basic(db, user_id)
    if next_row is not None:
        next_row.is_default = True
        db.add(next_row)
        db.commit()


def bulk_create(
    db: Session,
    user_id: int,
    items: list[Any],
    source: str = "manual",
) -> dict[str, int]:
    if not isinstance(items, list) or not items:
        raise AppException("没有需要保存的信息", 400)
    if len(items) > MAX_BULK_CREATE:
        raise AppException(f"一次最多保存 {MAX_BULK_CREATE} 条信息", 400)
    created = 0
    updated = 0
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            _, is_new = create_fragment(
                db,
                user_id,
                str(item.get("kind") or ""),
                item.get("payload"),
                title=item.get("title"),
                tags=item.get("tags"),
                source=str(item.get("source") or source),
            )
        except AppException:
            skipped += 1
            continue
        if is_new:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated, "skipped": skipped}


def _flatten_value(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    return str(value or "")


def extract_from_resume(db: Session, user_id: int, resume: Resume) -> dict[str, int]:
    """把简历里已有的内容拆成原子信息（已存在的跳过）。"""
    data = resume.resume_data or {}
    candidates: list[tuple[str, dict[str, Any]]] = []

    basics = data.get("basics")
    if isinstance(basics, dict) and any(
        str(basics.get(key) or "").strip() for key in ("name", "phone", "email", "title", "location")
    ):
        # 头像属于账号头像库（信息库页单独管理），不是一条可复用的文字事实，
        # 拆成 fragment 只会让「个人信息」里多出一个贴对象名的文本框。
        candidates.append(
            (
                "basic",
                {
                    key: value
                    for key, value in basics.items()
                    if key != "avatar" and isinstance(value, (str, int, float))
                },
            )
        )

    for section, kind in SECTION_TO_KIND.items():
        items = data.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            payload = {key: value for key, value in item.items() if key != "id"}
            if any(str(_flatten_value(value)).strip() for value in payload.values()):
                candidates.append((kind, payload))

    for section in data.get("custom_sections") or []:
        if not isinstance(section, dict):
            continue
        kind = PRESET_TO_KIND.get(str(section.get("preset_type") or ""), "custom")
        for item in section.get("items") or []:
            if not isinstance(item, dict):
                continue
            payload = {key: value for key, value in item.items() if key != "id"}
            if any(str(_flatten_value(value)).strip() for value in payload.values()):
                candidates.append((kind, payload))

    created = 0
    updated = 0
    skipped = 0
    for index, (kind, payload) in enumerate(candidates):
        if index >= MAX_EXTRACT_ITEMS:
            skipped += 1
            continue
        try:
            _, is_new = create_fragment(db, user_id, kind, payload, source="resume")
        except AppException:
            skipped += 1
            continue
        if is_new:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# 导入简历
# ---------------------------------------------------------------------------


def _item_key(payload: dict[str, Any]) -> str:
    """比较「同一条内容」时忽略 id 与空值，避免格式差异导致误判。"""
    material = {key: value for key, value in payload.items() if key != "id" and str(value or "").strip()}
    return json.dumps(material, ensure_ascii=False, sort_keys=True)


def _next_custom_id(data: dict[str, Any]) -> str:
    used = {
        str(item.get("id"))
        for item in (data.get("custom_sections") or [])
        if isinstance(item, dict) and item.get("id")
    }
    n = 1
    while f"custom_{n}" in used:
        n += 1
    return f"custom_{n}"


def _next_item_id(section: str, items: list[Any]) -> str:
    # 自定义模块的条目 id 用 citem 前缀，避免和模块 id（custom_N）撞名
    prefix = "citem" if section == "custom_sections" else (SECTIONS[section].id_prefix or section.rstrip("s"))
    used = {str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")}
    n = 1
    while f"{prefix}_{n}" in used:
        n += 1
    return f"{prefix}_{n}"


def _ensure_custom_section(data: dict[str, Any], kind: str) -> dict[str, Any]:
    spec = fragment_kind(kind)
    preset = spec.preset_type if spec else "custom"
    sections = data.setdefault("custom_sections", [])
    for section in sections:
        if isinstance(section, dict) and str(section.get("preset_type") or "") == preset:
            section.setdefault("items", [])
            return section
    new_id = _next_custom_id(data)
    section = {
        "id": new_id,
        "title": CUSTOM_SECTION_TITLES.get(kind, spec.label if spec else "自定义模块"),
        "preset_type": preset,
        "items": [],
    }
    sections.append(section)
    layout = data.setdefault("layout", {})
    order = layout.get("section_order")
    if isinstance(order, list):
        order.append(new_id)
    else:
        layout["section_order"] = ["basics", new_id]
    titles = layout.setdefault("section_titles", {})
    titles[new_id] = section["title"]
    return section


def _reveal_section(data: dict[str, Any], key: str) -> None:
    """导入是用户明确想看到这条内容，目标模块若被隐藏就顺手取消隐藏。

    简历编辑器按 ``layout.hidden_sections`` 决定模块是否渲染；只写数据不动这个
    标记，用户会看到「提示导入成功，但简历里什么都没出现」。
    """
    if not key:
        return
    layout = data.get("layout")
    if not isinstance(layout, dict):
        return
    hidden = layout.get("hidden_sections")
    if isinstance(hidden, list) and key in hidden:
        layout["hidden_sections"] = [item for item in hidden if item != key]


def merge_fragments_into_resume(
    current_data: dict[str, Any] | None,
    rows: list[InfoFragment],
    *,
    overwrite_placeholders: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """把原子信息追加到对应模块；返回 ``(merged_data, added_labels)``。

    basics 过去只填空白字段，模板自带的示例值（Elliot / zhangsan@example.com）
    会把导入挡在外面。``overwrite_placeholders`` 打开时，模板占位值也算「可写」，
    用户真实填过的内容依然不动（判断集中在 resume_placeholder）。
    """
    import copy

    data = copy.deepcopy(current_data or {})
    added: list[str] = []

    for row in rows:
        spec = fragment_kind(row.kind)
        if spec is None:
            continue
        payload = {key: value for key, value in (row.payload or {}).items() if str(value or "").strip()}
        if not payload:
            continue
        if spec.section == "basics":
            basics = data.setdefault("basics", {})
            changed = False
            for key, value in payload.items():
                if key not in SECTIONS["basics"].field_keys:
                    continue
                current = basics.get(key)
                if not is_blank(current) and not (overwrite_placeholders and is_placeholder_value(key, current)):
                    continue
                if str(current or "").strip() == str(value or "").strip():
                    continue
                basics[key] = value
                changed = True
            if changed:
                added.append(f"基本信息（{row.title}）")
            continue
        if spec.section == "custom_sections":
            section = _ensure_custom_section(data, row.kind)
            _reveal_section(data, "custom_sections")
            _reveal_section(data, str(section.get("id") or ""))
            items = section.setdefault("items", [])
            if any(_item_key(item) == _item_key(payload) for item in items if isinstance(item, dict)):
                continue
            item = dict(payload)
            item["id"] = _next_item_id(spec.section, items)
            items.append(item)
            added.append(f"{section.get('title') or '自定义模块'}（{row.title}）")
            continue
        section_items = data.get(spec.section)
        if not isinstance(section_items, list):
            section_items = []
            data[spec.section] = section_items
        # 命中去重也要显示模块：内容可能早就在简历里，只是被用户隐藏了。
        _reveal_section(data, spec.section)
        if any(_item_key(item) == _item_key(payload) for item in section_items if isinstance(item, dict)):
            continue
        item = dict(payload)
        item["id"] = _next_item_id(spec.section, section_items)
        section_items.append(item)
        added.append(f"{SECTIONS[spec.section].label}（{row.title}）")

    return data, added


def import_fragments(
    db: Session,
    user_id: int,
    resume: Resume,
    fragment_ids: list[int],
    base_data: dict[str, Any] | None = None,
    *,
    overwrite_placeholders: bool = True,
) -> tuple[dict[str, Any], list[str], list[InfoFragment]]:
    """把信息库条目并入简历快照。

    ``base_data`` 是「本轮已经生成的待确认快照」：模型一轮里先改简历、再导入
    信息库时，后者要叠在前者的结果上，而不是回到库里的旧数据重新算一遍。
    """
    ids: list[int] = []
    for raw in fragment_ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    if not ids:
        raise AppException("请选择要导入的信息", 400)
    if len(ids) > 20:
        raise AppException("一次最多导入 20 条信息", 400)

    rows: list[InfoFragment] = []
    for fragment_id in ids:
        rows.append(_user_row(db, user_id, fragment_id))

    merged, added = merge_fragments_into_resume(
        base_data or resume.resume_data, rows, overwrite_placeholders=overwrite_placeholders
    )
    # 「已存在」不是错误：即便没有新增条目，也要把目标模块从 hidden_sections 里
    # 放出来并写回，否则用户看到的是「导入了但简历里找不到」。是否真的新增由
    # added 告知调用方，前端据此给出不同提示。
    normalized = normalize_resume_document(merged, {"language": resume.language})
    return normalized, added, rows


def touch_usage(db: Session, rows: list[InfoFragment]) -> None:
    now = datetime.now()
    for row in rows:
        row.usage_count = (row.usage_count or 0) + 1
        row.last_used_time = now
        db.add(row)
    db.commit()


def search_for_agent(
    db: Session,
    user_id: int,
    query: str | None,
    kinds: list[str] | None = None,
    limit: int = 8,
) -> list[InfoFragment]:
    """给 Agent 的检索：先按关键词筛，再按命中次数与使用频率排序。"""
    safe_limit = max(1, min(int(limit or 8), 20))
    filters = [InfoFragment.user_id == user_id, InfoFragment.enabled.is_(True)]
    valid_kinds = [kind for kind in (kinds or []) if kind in FRAGMENT_KINDS]
    if valid_kinds:
        filters.append(InfoFragment.kind.in_(valid_kinds))
    text = re.sub(r"\s+", " ", str(query or "")).strip()[:60]

    if not text:
        rows = list(
            db.scalars(
                select(InfoFragment)
                .where(*filters)
                .order_by(
                    InfoFragment.is_default.desc(), InfoFragment.usage_count.desc(), InfoFragment.update_time.desc()
                )
                .limit(safe_limit)
            )
        )
        return rows

    pattern = f"%{_escape_like(text)}%"
    candidates = list(
        db.scalars(
            select(InfoFragment)
            .where(*filters, InfoFragment.search_text.like(pattern, escape="\\"))
            .order_by(InfoFragment.update_time.desc())
            .limit(200)
        )
    )
    terms = [term for term in re.split(r"[\s，,、/]+", text) if term]

    def score(row: InfoFragment) -> tuple[int, int, int, float]:
        haystack = row.search_text or ""
        title = row.title or ""
        hits = sum(1 for term in terms if term and term in haystack)
        title_hits = sum(1 for term in terms if term and term in title)
        # 默认条目排在同分之前：问「我的邮箱是什么」时，别让某份旧简历里的
        # 重复个人信息抢在前面。
        return (1 if row.is_default else 0, title_hits, hits, row.usage_count or 0)

    candidates.sort(key=score, reverse=True)
    if candidates:
        return candidates[:safe_limit]

    # 没命中关键词时给最近使用/最近更新的一批，避免 Agent 直接编造「没找到」
    return list(
        db.scalars(
            select(InfoFragment)
            .where(*filters)
            .order_by(
                InfoFragment.is_default.desc(), InfoFragment.usage_count.desc(), InfoFragment.update_time.desc()
            )
            .limit(min(3, safe_limit))
        )
    )


def validate_proposals(db: Session, user_id: int, proposals: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """校验 Agent 提议的新增/更新条目，返回 ``(pending_items, issues)``。

    ``id`` 指向已有条目时按更新处理（不允许改别人的条目），否则视为新增。
    """
    pending: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[str] = set()
    proposals = (proposals or [])[:10]
    for index, item in enumerate(proposals or [], start=1):
        if not isinstance(item, dict):
            issues.append(f"第 {index} 条不是有效的信息对象")
            continue
        kind = str(item.get("kind") or "").strip().lower() or guess_fragment_kind(item.get("payload"))
        spec = fragment_kind(kind)
        if spec is None:
            issues.append(f"第 {index} 条的信息分类不支持：{kind or '空'}")
            continue
        payload = item.get("payload")
        cleaned, issue = normalize_fragment_payload(kind, payload)
        if issue:
            issues.append(f"第 {index} 条：{issue}")
            continue
        values = _build_row_values(kind, cleaned, item.get("title"), item.get("tags"))
        if values["fingerprint"] in seen:
            continue
        seen.add(values["fingerprint"])

        existing_id = item.get("id") or item.get("fragment_id")
        target_id: int | None = None
        if existing_id is not None:
            try:
                target_id = int(existing_id)
            except (TypeError, ValueError):
                target_id = None
            if target_id is not None:
                row = db.get(InfoFragment, target_id)
                if not row or row.user_id != user_id:
                    issues.append(f"第 {index} 条要更新的信息不存在")
                    continue
        pending.append(
            {
                "id": target_id,
                "kind": spec.kind,
                "kind_label": spec.label,
                "title": values["title"],
                "summary": values["summary"],
                "payload": values["payload"],
                "tags": values["tags"],
                "source": "chat",
            }
        )
    return pending, issues


def apply_proposals(db: Session, user_id: int, pending: list[dict[str, Any]]) -> dict[str, int]:
    created = 0
    updated = 0
    for item in pending:
        if item.get("id"):
            update_fragment(
                db,
                user_id,
                int(item["id"]),
                kind=item.get("kind"),
                payload=item.get("payload"),
                title=item.get("title"),
                tags=item.get("tags"),
            )
            updated += 1
            continue
        _, is_new = create_fragment(
            db,
            user_id,
            str(item.get("kind") or ""),
            item.get("payload"),
            title=item.get("title"),
            tags=item.get("tags"),
            source="chat",
        )
        if is_new:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}
