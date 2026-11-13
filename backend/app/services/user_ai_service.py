"""BYOK 凭证与用户技能的存取、校验和提示词拼装。

这一层刻意不碰 FastAPI：路由只做鉴权和参数转换，业务规则（多配置上限、
默认项、掩码、启用过滤、提示词格式、技能包解析）都在这里，方便直接用
session 做单元测试。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.core.exceptions import AppException
from app.core.url_guard import literal_public_url, probe_public_url
from app.models.user_ai import UserAiCredential, UserSkill
from app.schemas.user_ai import (
    MAX_MODELS_PER_USER,
    MAX_SKILL_INSTRUCTIONS,
    REASONING_LEVELS,
    UserAiCredentialIn,
    UserSkillIn,
    UserSkillUpdateIn,
)

# 技能拼进系统提示词时的整体上限：超出就截断，避免把上下文吃光。
MAX_SKILL_PROMPT_CHARS = 8_000

# 技能包（zip / 文件夹）的安全上限。压缩比再高也不允许在内存里展开成炸弹。
MAX_PACKAGE_COMPRESSED_BYTES = 5 * 1024 * 1024
MAX_PACKAGE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_SINGLE_FILE_BYTES = 128 * 1024
MAX_PACKAGE_ENTRIES = 200
MAX_PACKAGE_SKILLS = 20
TEXT_SUFFIXES = {".md", ".markdown", ".txt"}


# ---------------------------------------------------------------------------
# 多模型凭证
# ---------------------------------------------------------------------------


def list_credentials(db: Session, user_id: int) -> list[UserAiCredential]:
    return list(
        db.scalars(
            select(UserAiCredential)
            .where(UserAiCredential.user_id == user_id)
            .order_by(UserAiCredential.is_default.desc(), UserAiCredential.id.asc())
        )
    )


def get_credential(db: Session, user_id: int, credential_id: int | None = None) -> Optional[UserAiCredential]:
    """默认项 / 指定项的宽松解析，供对话链路使用；找不到一律返回 None。"""
    if credential_id is not None:
        row = db.get(UserAiCredential, credential_id)
        return row if row and row.user_id == user_id else None
    rows = list_credentials(db, user_id)
    return next((row for row in rows if row.enabled), rows[0] if rows else None)


def require_credential(db: Session, user_id: int, credential_id: int) -> UserAiCredential:
    row = db.get(UserAiCredential, credential_id)
    if not row or row.user_id != user_id:
        raise AppException("模型配置不存在", 404)
    return row


def _clear_defaults(db: Session, user_id: int, except_id: int | None = None) -> None:
    for row in list_credentials(db, user_id):
        if row.is_default and row.id != except_id:
            row.is_default = False


def _ensure_default(db: Session, user_id: int) -> None:
    """任何时刻最多一个默认项；删掉默认项或取消勾选后自动补位。"""
    rows = list_credentials(db, user_id)
    if any(row.is_default for row in rows):
        return
    if rows:
        rows[0].is_default = True


def _apply_fields(row: UserAiCredential, payload: UserAiCredentialIn, *, api_key: str | None) -> None:
    if api_key:
        row.api_key_masked = mask_secret(api_key)
        row.api_key_encrypted = encrypt_secret(api_key)
    row.provider_label = payload.provider_label
    row.base_url = payload.base_url
    row.model = payload.model
    row.context_window = payload.context_window
    row.max_tokens = payload.max_tokens
    row.supports_image = payload.supports_image
    row.reasoning_effort = payload.reasoning_effort
    row.enabled = payload.enabled


def create_credential(db: Session, user_id: int, payload: UserAiCredentialIn) -> UserAiCredential:
    api_key = (payload.api_key or "").strip()
    if not api_key:
        raise AppException("新增模型配置需要填写 API Key", 400)
    count = db.scalar(
        select(func.count()).select_from(UserAiCredential).where(UserAiCredential.user_id == user_id)
    ) or 0
    if count >= MAX_MODELS_PER_USER:
        raise AppException(f"每个用户最多保存 {MAX_MODELS_PER_USER} 个模型配置", 400)

    row = UserAiCredential(
        user_id=user_id,
        api_key_masked="",
        api_key_encrypted="",
        is_default=bool(payload.is_default) or count == 0,
    )
    _apply_fields(row, payload, api_key=api_key)
    if row.is_default:
        _clear_defaults(db, user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_credential(db: Session, user_id: int, credential_id: int, payload: UserAiCredentialIn) -> UserAiCredential:
    row = require_credential(db, user_id, credential_id)
    api_key = (payload.api_key or "").strip()
    if not api_key and not row.api_key_encrypted:
        raise AppException("请先填写 API Key", 400)
    _apply_fields(row, payload, api_key=api_key or None)
    if payload.is_default:
        _clear_defaults(db, user_id, except_id=row.id)
        row.is_default = True
    else:
        # 取消勾选时把默认项让给另一条启用中的配置；只有一条时 _ensure_default
        # 会自动补回自己，保证「任意时刻恰好一个默认项」。
        row.is_default = False
        db.flush()
        candidate = next(
            (item for item in list_credentials(db, user_id) if item.id != row.id and item.enabled),
            None,
        )
        if candidate:
            candidate.is_default = True
    db.commit()
    db.refresh(row)
    _ensure_default(db, user_id)
    db.commit()
    db.refresh(row)
    return row


def upsert_credential(db: Session, user_id: int, payload: UserAiCredentialIn) -> UserAiCredential:
    """旧的单配置接口：更新默认项，没有则创建。保留仅为兼容既有客户端。"""
    row = get_credential(db, user_id)
    if not row:
        return create_credential(db, user_id, payload)
    payload.is_default = row.is_default
    return update_credential(db, user_id, row.id, payload)


def delete_credential(db: Session, user_id: int, credential_id: int | None = None) -> bool:
    row = (
        require_credential(db, user_id, credential_id)
        if credential_id is not None
        else get_credential(db, user_id)
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    _ensure_default(db, user_id)
    db.commit()
    return True


def set_default_credential(db: Session, user_id: int, credential_id: int) -> UserAiCredential:
    row = require_credential(db, user_id, credential_id)
    _clear_defaults(db, user_id, except_id=row.id)
    row.is_default = True
    db.commit()
    db.refresh(row)
    return row


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
            # 404 / 其它状态码：不少兼容层没有 /models，直接打一次最小对话请求。
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


def resolve_model_override(
    db: Session, user_id: int, credential_id: int | None = None
) -> Optional[dict[str, Any]]:
    """对话前解析用户自带模型；未配置/已关闭/解不开密钥都返回 None（走平台默认）。

    ``credential_id=0`` 是前端「明确使用平台模型」的标记；传其它 id 则临时
    切换到该条配置（不改变默认项）。
    """
    if credential_id == 0:
        return None
    credential = get_credential(db, user_id, credential_id)
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
        "credential_id": credential.id,
        "model": credential.model,
        "base_url": credential.base_url,
        "api_key": api_key,
        "name": f"{credential.provider_label} · {credential.model}",
        "context_window": credential.context_window,
        "max_tokens": credential.max_tokens,
        "supports_image": credential.supports_image,
        "reasoning_effort": credential.reasoning_effort if credential.reasoning_effort in REASONING_LEVELS | {"off"} else "auto",
    }


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------


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

    name = PurePosixPath(filename or "skill").stem.strip() or "自定义技能"
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


# ---------------------------------------------------------------------------
# 技能包：zip 或文件夹
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillPackageFile:
    path: str
    content: str


def _decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError as exc:
            raise AppException("文件编码无法识别，请另存为 UTF-8", 400) from exc


def _safe_member_path(name: str) -> str | None:
    """归一化 zip/文件夹里的相对路径；拒绝绝对路径、`..`、盘符和隐藏垃圾。"""
    normalized = (name or "").replace("\\", "/").lstrip("/")
    if not normalized or "\x00" in normalized:
        return None
    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or re.match(r"^[A-Za-z]:$", part):
            return None
        parts.append(part)
    if not parts or parts[0].startswith("__MACOSX") or parts[0] == ".DS_Store":
        return None
    path = "/".join(parts)
    if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
        return None
    return path


def collect_zip_files(raw: bytes) -> tuple[list[SkillPackageFile], list[str]]:
    """展开 zip，但只读取安全的文本文件；返回 (文件列表, 跳过说明)。"""
    if len(raw) > MAX_PACKAGE_COMPRESSED_BYTES:
        raise AppException("技能包压缩文件不能超过 5 MB", 400)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise AppException("无法识别 ZIP 包，请确认文件完整", 400) from exc

    files: list[SkillPackageFile] = []
    skipped: list[str] = []
    total = 0
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_PACKAGE_ENTRIES:
            raise AppException(f"技能包最多包含 {MAX_PACKAGE_ENTRIES} 个文件", 400)
        declared = sum(item.file_size for item in entries)
        if declared > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            raise AppException("技能包解压后内容过大（上限 2 MB）", 400)
        for info in entries:
            # 符号链接（Unix mode 高位为 0o120000）不跟随，避免读到包外内容。
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                skipped.append(f"{info.filename}（符号链接已忽略）")
                continue
            if info.is_dir():
                continue
            path = _safe_member_path(info.filename)
            if not path:
                continue
            if info.file_size > MAX_PACKAGE_SINGLE_FILE_BYTES:
                skipped.append(f"{path}（单文件超过 128 KB）")
                continue
            try:
                data = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile):
                skipped.append(f"{path}（读取失败）")
                continue
            total += len(data)
            if total > MAX_PACKAGE_UNCOMPRESSED_BYTES:
                raise AppException("技能包解压后内容过大（上限 2 MB）", 400)
            files.append(SkillPackageFile(path=path, content=_decode_text(data)))
    return files, skipped


def collect_folder_files(entries: list[tuple[str, bytes]]) -> tuple[list[SkillPackageFile], list[str]]:
    """浏览器 webkitdirectory 多选：文件名带相对路径，按同一套规则清洗。"""
    if len(entries) > MAX_PACKAGE_ENTRIES:
        raise AppException(f"技能文件夹最多包含 {MAX_PACKAGE_ENTRIES} 个文件", 400)
    total = sum(len(data) for _, data in entries)
    if total > MAX_PACKAGE_UNCOMPRESSED_BYTES:
        raise AppException("技能文件夹内容过大（上限 2 MB）", 400)
    files: list[SkillPackageFile] = []
    skipped: list[str] = []
    for name, data in entries:
        path = _safe_member_path(name)
        if not path:
            continue
        if len(data) > MAX_PACKAGE_SINGLE_FILE_BYTES:
            skipped.append(f"{path}（单文件超过 128 KB）")
            continue
        files.append(SkillPackageFile(path=path, content=_decode_text(data)))
    return files, skipped


def parse_skill_package(files: list[SkillPackageFile]) -> tuple[list[UserSkillIn], list[str]]:
    """把技能包拆成若干技能：以每个 SKILL.md 为根，附属文本作为参考资料拼入正文。

    WorkBuddy 风格的技能目录约定：``<skill-name>/SKILL.md`` + 可选的
    ``references/*.md`` / ``examples/*.md``。这里不执行任何脚本，只把可读的
    文本资料合并进指令，避免用户上传的包变成服务端可执行内容。
    """
    if not files:
        raise AppException("技能包里没有可用的 .md / .markdown / .txt 文件", 400)

    manifests = [item for item in files if PurePosixPath(item.path).name.lower() == "skill.md"]
    warnings: list[str] = []
    if not manifests:
        if len(files) > 1:
            raise AppException("技能包里没找到 SKILL.md；多个文件时请按「目录/SKILL.md」组织", 400)
        single = files[0]
        return [parse_skill_file(single.content, single.path)], warnings

    if len(manifests) > MAX_PACKAGE_SKILLS:
        raise AppException(f"单个技能包最多导入 {MAX_PACKAGE_SKILLS} 个技能", 400)

    skills: list[UserSkillIn] = []
    manifest_paths = {item.path for item in manifests}
    for manifest in sorted(manifests, key=lambda item: item.path):
        root = str(PurePosixPath(manifest.path).parent)
        prefix = "" if root == "." else f"{root}/"
        # 目录名作兜底名称：frontmatter 没写 name 时比 "skill" 可读得多。
        parsed = parse_skill_file(manifest.content, PurePosixPath(root).name if prefix else "skill")

        extras = [
            item
            for item in files
            if item.path.startswith(prefix)
            and item.path not in manifest_paths
            and item.content.strip()
        ]
        extras.sort(key=lambda item: item.path)
        if extras:
            blocks: list[str] = []
            remaining = MAX_SKILL_INSTRUCTIONS - len(parsed.instructions) - 40
            for item in extras:
                title = PurePosixPath(item.path).name.replace("\r", " ").replace("\n", " ")
                section = f"### {title}\n{item.content.strip()}"
                if remaining <= 0:
                    warnings.append("参考资料较多，超出正文上限的部分已省略")
                    break
                blocks.append(section[:remaining])
                remaining -= len(blocks[-1])
            if blocks:
                parsed.instructions = f"{parsed.instructions}\n\n## 参考资料\n\n" + "\n\n".join(blocks)
        skills.append(parsed)
    return skills, warnings
