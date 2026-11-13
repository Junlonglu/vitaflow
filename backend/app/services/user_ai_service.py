"""BYOK 凭证与用户技能的存取、校验和提示词拼装。

这一层刻意不碰 FastAPI：路由只做鉴权和参数转换，业务规则（唯一性、掩码、
启用过滤、提示词格式）都在这里，方便直接用 session 做单元测试。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.core.exceptions import AppException
from app.core.url_guard import literal_public_url, probe_public_url
from app.models.user_ai import UserAiCredential, UserSkill
from app.schemas.user_ai import MAX_SKILL_INSTRUCTIONS, UserAiCredentialIn, UserSkillIn, UserSkillUpdateIn

# 技能拼进系统提示词时的整体上限：超出就截断，避免把上下文吃光。
MAX_SKILL_PROMPT_CHARS = 8_000


def get_credential(db: Session, user_id: int) -> Optional[UserAiCredential]:
    return db.scalar(select(UserAiCredential).where(UserAiCredential.user_id == user_id))


def upsert_credential(db: Session, user_id: int, payload: UserAiCredentialIn) -> UserAiCredential:
    credential = get_credential(db, user_id)
    api_key = (payload.api_key or "").strip()
    if not credential and not api_key:
        raise AppException("首次配置需要填写 API Key", 400)
    if credential and not api_key:
        api_key_masked = credential.api_key_masked
        api_key_encrypted = credential.api_key_encrypted
    else:
        api_key_masked = mask_secret(api_key)
        api_key_encrypted = encrypt_secret(api_key)

    if not credential:
        credential = UserAiCredential(user_id=user_id, api_key_masked="", api_key_encrypted="")
        db.add(credential)

    credential.provider_label = payload.provider_label
    credential.base_url = payload.base_url
    credential.model = payload.model
    credential.api_key_masked = api_key_masked
    credential.api_key_encrypted = api_key_encrypted
    credential.context_window = payload.context_window
    credential.max_tokens = payload.max_tokens
    credential.supports_image = payload.supports_image
    credential.enabled = payload.enabled
    db.commit()
    db.refresh(credential)
    return credential


def delete_credential(db: Session, user_id: int) -> bool:
    credential = get_credential(db, user_id)
    if not credential:
        return False
    db.delete(credential)
    db.commit()
    return True


def mark_verified(db: Session, credential: UserAiCredential) -> None:
    from datetime import datetime

    credential.last_verified_time = datetime.now()
    db.commit()
    db.refresh(credential)


async def probe_credential(
    base_url: str,
    api_key: str,
    model: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 15.0,
) -> tuple[bool, str, list[str]]:
    """用 /models 探活；对方没实现时退回一次 1 token 的对话请求。

    返回 (是否可用, 说明, 模型名列表)。网络错误一律翻译成人话，不把底层异常
    直接抛给前端。
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    models: list[str] = []
    allowed, reason = await asyncio.to_thread(probe_public_url, base_url)
    if not allowed:
        return False, reason, []
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {}
                rows = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(rows, list):
                    models = [str(item.get("id")) for item in rows if isinstance(item, dict) and item.get("id")]
                if models and model not in models:
                    return True, f"接入可用，但模型列表里没有 {model}，请确认名称是否正确", models[:50]
                return True, "接入可用", models[:50]
            if resp.status_code in (401, 403):
                return False, "API Key 被拒绝，请检查是否填写正确或是否已过期", []
            if resp.status_code == 404:
                # 不少兼容层没有 /models，直接打一次最小对话请求。
                pass
            else:
                # 其他状态码也继续用对话请求确认一次，避免 /models 权限策略误判。
                pass

            chat = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={**headers, "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            )
            if chat.status_code in (401, 403):
                return False, "API Key 被拒绝，请检查是否填写正确或是否已过期", []
            if chat.status_code >= 400:
                detail = chat.text[:160].strip() or f"HTTP {chat.status_code}"
                return False, f"模型调用失败：{detail}", []
            return True, "接入可用（已通过对话请求验证）", []
    except httpx.TimeoutException:
        return False, "连接超时，请检查接入地址网络是否可达", []
    except httpx.HTTPError as exc:
        return False, f"连接失败：{exc}", []


def resolve_model_override(db: Session, user_id: int) -> Optional[dict[str, Any]]:
    """对话前解析用户自带模型；未配置/已关闭/解不开密钥都返回 None（走平台默认）。"""
    credential = get_credential(db, user_id)
    if not credential or not credential.enabled:
        return None
    allowed, _ = literal_public_url(credential.base_url)
    if not allowed:
        return None
    api_key = decrypt_secret(credential.api_key_encrypted)
    if not api_key:
        return None
    return {
        "provider": f"byok-{user_id}",
        "model": credential.model,
        "base_url": credential.base_url,
        "api_key": api_key,
        "name": f"{credential.provider_label} · {credential.model}",
        "context_window": credential.context_window,
        "max_tokens": credential.max_tokens,
        "supports_image": credential.supports_image,
    }


def _slugify(name: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if ascii_part:
        return ascii_part[:60]
    # 中文名没有可用的 ASCII；用名称派生稳定短哈希，同名技能不会每次导入都换 slug。
    digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:8]
    return f"skill-{digest}"


def _unique_slug(db: Session, user_id: int, base: str, exclude_id: int | None = None) -> str:
    slug = base
    index = 1
    while True:
        existing = db.scalar(
            select(UserSkill).where(UserSkill.user_id == user_id, UserSkill.slug == slug)
        )
        if not existing or (exclude_id is not None and existing.id == exclude_id):
            return slug
        index += 1
        slug = f"{base}-{index}"[:80]


def list_skills(db: Session, user_id: int) -> list[UserSkill]:
    return list(
        db.scalars(
            select(UserSkill)
            .where(UserSkill.user_id == user_id)
            .order_by(UserSkill.enabled.desc(), UserSkill.update_time.desc())
        )
    )


def get_skill(db: Session, user_id: int, skill_id: int) -> UserSkill:
    skill = db.get(UserSkill, skill_id)
    if not skill or skill.user_id != user_id:
        raise AppException("技能不存在", 404)
    return skill


def create_skill(db: Session, user_id: int, payload: UserSkillIn, source: str = "manual") -> UserSkill:
    slug = _unique_slug(db, user_id, _slugify(payload.name))
    skill = UserSkill(
        user_id=user_id,
        slug=slug,
        name=payload.name,
        description=payload.description.strip(),
        instructions=payload.instructions,
        enabled=payload.enabled,
        source=source,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


def update_skill(db: Session, user_id: int, skill_id: int, payload: UserSkillUpdateIn) -> UserSkill:
    skill = get_skill(db, user_id, skill_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise AppException("技能名称不能为空", 400)
        skill.name = name
    if payload.description is not None:
        skill.description = payload.description.strip()
    if payload.instructions is not None:
        text = payload.instructions.strip()
        if not text:
            raise AppException("技能内容不能为空", 400)
        skill.instructions = text
    if payload.enabled is not None:
        skill.enabled = payload.enabled
    db.commit()
    db.refresh(skill)
    return skill


def delete_skill(db: Session, user_id: int, skill_id: int) -> bool:
    skill = get_skill(db, user_id, skill_id)
    db.delete(skill)
    db.commit()
    return True


def build_skills_payload(db: Session, user_id: int) -> list[dict[str, str]]:
    """只带启用中的技能，按更新时间排序，总长度超限时截断后续技能。"""
    payload: list[dict[str, str]] = []
    used = 0
    for skill in list_skills(db, user_id):
        if not skill.enabled:
            continue
        body = (skill.instructions or "").strip()
        if not body:
            continue
        remain = MAX_SKILL_PROMPT_CHARS - used
        if remain <= 0:
            break
        trimmed = body[:remain]
        payload.append({"name": skill.name, "description": skill.description or "", "instructions": trimmed})
        used += len(trimmed)
    return payload


# 兼容主流 Agent Skills 的 SKILL.md 写法：YAML 风格的极简 frontmatter，只认
# name / description 两个键，其余行忽略（不引入 yaml 依赖，也不执行任何内容）。
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def parse_skill_file(content: str, filename: str = "") -> UserSkillIn:
    """把上传的 .md/.txt 解析成技能；文件名作默认名称，frontmatter 可覆盖。"""
    text = (content or "").replace("\r\n", "\n").strip()
    if not text:
        raise AppException("技能文件内容为空", 400)
    if len(text) > MAX_SKILL_INSTRUCTIONS + 2_000:
        raise AppException(f"技能文件过大，正文请控制在 {MAX_SKILL_INSTRUCTIONS} 字以内", 400)

    name = Path(filename or "skill").stem.strip() or "自定义技能"
    description = ""
    body = text
    match = _FRONTMATTER_RE.match(text)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            value = value.strip().strip("\"'")
            if key.strip().lower() == "name" and value:
                name = value
            elif key.strip().lower() == "description" and value:
                description = value
        body = match.group(2).strip()
    if not body:
        raise AppException("技能正文为空，请在 frontmatter 之后填写指令内容", 400)

    return UserSkillIn(
        name=name[:60],
        description=description[:255],
        instructions=body[:MAX_SKILL_INSTRUCTIONS],
        enabled=True,
    )
