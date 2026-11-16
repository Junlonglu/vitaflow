"""简历 JSON 导出与回填。

PDF 走 Chromium 渲染，JSON 不做渲染：直接把落库的结构化数据原样序列化，让用户
能把一份简历完整带走（换账号、备份、外部分析）。因此导出载荷必须包含还原所需的
全部字段，而 ``id`` / ``user_id`` / ``share_token`` 一律不带——前者导入时会和现有
行冲突，后者等于把分享凭证塞进一个会被随意转发的文件里。

导入按「导出文件原样回填」设计：不做 AI 解析、不消耗积分，与 PDF/Word 导入
（抽文本 → 大模型结构化）是两条完全不同的链路。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.resume_schema import LIST_SECTIONS, SCHEMA_VERSION
from app.core.template_config import normalize_template_config
from app.models.resume import Resume
from app.schemas.resume import ResumeCreate, default_template_config
from app.services.resume_import_service import derive_imported_resume_title
from app.services.resume_locale import normalize_resume_language
from app.services.resume_service import create_resume
from app.services.template_service import get_template

EXPORT_FORMAT = "resumeflow-resume"
EXPORT_FORMAT_VERSION = 1
# 不落盘的导出在 export_records.file_path 里留这个标记，后台据此知道没有可下载文件。
INLINE_FILE_PATH = "inline:json"
DEFAULT_TEMPLATE_ID = "tech"

_NOT_A_RESUME_MESSAGE = "JSON 内容不是一份简历，请选择 ResumeFlow 导出的 .json 文件"


def build_export_payload(resume: Resume) -> dict[str, Any]:
    resume_data = resume.resume_data if isinstance(resume.resume_data, dict) else {}
    template_config = resume.template_config if isinstance(resume.template_config, dict) else {}
    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        # 顶层再放一份 schema 版本，外部工具不必先拆 resume_data 就能判断契约。
        "schema_version": str(resume_data.get("schema_version") or SCHEMA_VERSION),
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "resume": {
            "title": resume.title,
            "language": normalize_resume_language(resume.language),
            "template_id": resume.template_id,
            "template_config": template_config,
            "resume_data": resume_data,
        },
    }


def export_json_bytes(resume: Resume) -> bytes:
    # ensure_ascii=False：中文原样落文件，用户直接打开就能读，体积也小一半。
    return json.dumps(build_export_payload(resume), ensure_ascii=False, indent=2).encode("utf-8")


def export_file_name(resume: Resume) -> str:
    stem = "".join(char for char in str(resume.title or "") if char not in '\\/:*?"<>|').strip()
    return f"{(stem or f'resume_{resume.id}')[:80]}.json"


def _looks_like_resume_data(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("basics"), dict):
        return True
    return any(isinstance(value.get(key), list) for key in LIST_SECTIONS)


def parse_resume_export(data: bytes, fallback_title: str = "") -> dict[str, Any]:
    """解析导出文件，返回 ``create_resume`` 需要的字段。

    同时接受两种形状：带 ``resume`` 包裹的导出文件，以及顶层的裸 ``resume_data``
    （手写或第三方生成的简历 JSON）。两者都认不出来就报错，绝不静默建一份空简历。
    """
    if not data:
        raise AppException("上传文件为空", 400)
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise AppException("JSON 文件需为 UTF-8 编码", 400) from exc
    except json.JSONDecodeError as exc:
        raise AppException(f"JSON 文件格式不正确（第 {exc.lineno} 行）", 400) from exc

    if not isinstance(payload, dict):
        raise AppException(_NOT_A_RESUME_MESSAGE, 400)

    source = payload.get("resume") if isinstance(payload.get("resume"), dict) else payload
    resume_data = source.get("resume_data") if isinstance(source.get("resume_data"), dict) else source
    if not _looks_like_resume_data(resume_data):
        raise AppException(_NOT_A_RESUME_MESSAGE, 400)

    language = normalize_resume_language(source.get("language"))
    template_config = source.get("template_config") if isinstance(source.get("template_config"), dict) else {}
    return {
        "title": str(source.get("title") or "").strip(),
        "language": language,
        "template_id": str(source.get("template_id") or "").strip(),
        "template_config": template_config,
        "resume_data": resume_data,
        "fallback_title": Path(fallback_title).stem.strip() if fallback_title else "",
    }


def _resolve_template_id(db: Session | None, *candidates: str) -> str:
    # 导出文件里的模板可能已被管理员隐藏/删除，逐个回落到仍然存在的那一个。
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and get_template(value, db) is not None:
            return value
    return DEFAULT_TEMPLATE_ID


def import_resume_from_json(
    db: Session,
    user_id: int,
    data: bytes,
    filename: str,
    template_id: str = "",
) -> Resume:
    payload = parse_resume_export(data, fallback_title=filename)
    resolved_template = _resolve_template_id(db, payload["template_id"], template_id)
    # 配置要过白名单收敛：它会进 <style>，导出文件可能被手改过。
    config = normalize_template_config(payload["template_config"] or default_template_config(resolved_template))
    config["template_id"] = resolved_template
    language = payload["language"]
    title = payload["title"] or derive_imported_resume_title(payload["resume_data"], payload["fallback_title"], language)
    return create_resume(
        db,
        user_id,
        ResumeCreate(
            title=title[:100],
            language=language,
            template_id=resolved_template,
            resume_data=payload["resume_data"],
            template_config=config,
        ),
    )
