from __future__ import annotations

import logging
from io import BytesIO
from typing import Iterator
from uuid import uuid4

from fastapi import UploadFile
from fastapi.responses import StreamingResponse
from minio import Minio
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import contracts
from app.core.config import settings
from app.core.signed_url import append_signature
from app.core.exceptions import AppException
from app.models.resume import UploadedFile


logger = logging.getLogger(__name__)

ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
# 三个图片上传口（头像 / 公告图 / 对话图）共用这一个上限，别再各写一个字面量。
# 值来自 core.contracts：前端附件校验与 Node 的 body 上限都按它推导。
MAX_SIZE = contracts.UPLOAD_MAX_BYTES
# 报错文案跟着常量走，改上限时不会漏改提示语。
MAX_SIZE_MB = contracts.UPLOAD_MAX_MB
# 一个账号最多保留多少张头像。头像库的「删除」只清索引不删对象，所以这里限制的
# 是可选项数量，避免用户反复上传把库和存储都堆满。
MAX_AVATARS_PER_USER = 30
CHUNK_SIZE = 32 * 1024
CACHE_MAX_AGE = 31536000
CACHE_CONTROL = f"public, max-age={CACHE_MAX_AGE}, immutable"

# 桶里可以对外代理的对象前缀。上传入口只会写这几种，白名单把「拿任意 key
# 去读桶」挡在业务层，路径穿越（..、绝对路径、反斜杠）也一并拒绝。
AVATAR_OBJECT_PREFIX = "avatar/"
PUBLIC_OBJECT_PREFIXES = (AVATAR_OBJECT_PREFIX, "announcement/")
PRIVATE_OBJECT_PREFIXES = ("ai-chat/",)
ALLOWED_OBJECT_PREFIXES = (*PUBLIC_OBJECT_PREFIXES, *PRIVATE_OBJECT_PREFIXES)


def is_allowed_object_name(object_name: str) -> bool:
    """对象名是否落在受管的业务前缀内；顺带挡住路径穿越写法。"""
    value = (object_name or "").strip()
    if not value or len(value) > 500:
        return False
    if value.startswith("/") or "\\" in value or ".." in value or "//" in value:
        return False
    return value.startswith(ALLOWED_OBJECT_PREFIXES)


def is_private_object_name(object_name: str) -> bool:
    """私有对象（当前只有对话图片）需要登录且只能被上传者读取。"""
    return (object_name or "").strip().startswith(PRIVATE_OBJECT_PREFIXES)


def owned_object_names(db: Session, user_id: int, object_names: list[str]) -> set[str]:
    """从候选对象名里挑出确实属于该用户的上传记录。

    这是「前端传来的 object_name 不可信」的唯一收敛点：调用方拿到返回值后
    只能读取集合内的对象，越权名在这里被静默丢弃（不报错，避免探测）。
    """
    candidates = {name for name in object_names if name}
    if not candidates:
        return set()
    rows = db.scalars(
        select(UploadedFile.object_name).where(
            UploadedFile.user_id == user_id,
            UploadedFile.object_name.in_(candidates),
        )
    )
    return set(rows.all())


def owned_upload_total_bytes(db: Session, user_id: int, object_names: list[str]) -> int:
    """这些对象里属于该用户的那些，合计多少字节。

    只用来判断「一次消息的附图总量」是否超预算（RF-044）。用 SQL 求和而不是
    把行读回来：一条消息最多引用 9 个对象，但路径可能是重试，没必要为此多拉数据。
    """
    candidates = {name for name in object_names if name}
    if not candidates:
        return 0
    total = db.scalar(
        select(func.coalesce(func.sum(UploadedFile.file_size), 0)).where(
            UploadedFile.user_id == user_id,
            UploadedFile.object_name.in_(candidates),
        )
    )
    return int(total or 0)


def _provider() -> str:
    provider = (settings.storage_provider or "minio").strip().lower()
    if provider not in {"minio", "aliyun_oss"}:
        raise AppException("STORAGE_PROVIDER 只能配置为 minio 或 aliyun_oss", 500)
    return provider


def _proxy_url(object_name: str) -> str:
    return f"{settings.pdf_base_url.rstrip('/')}/api/files/{object_name}"


def _public_url(object_name: str) -> str:
    if _provider() == "aliyun_oss":
        base_url = settings.aliyun_oss_public_url.strip()
        if base_url:
            return f"{base_url.rstrip('/')}/{object_name}"
    else:
        base_url = settings.minio_public_url.strip()
        if base_url:
            return f"{base_url.rstrip('/')}/{object_name}"
    return _proxy_url(object_name)


def _file_url(object_name: str) -> str:
    mode = (settings.storage_public_url_mode or "proxy").strip().lower()
    return _public_url(object_name) if mode == "public" else _proxy_url(object_name)


def _stream_cache_headers(object_name: str) -> dict[str, str]:
    safe_etag = object_name.replace('"', "")
    # 私有对象（对话图片）是「先鉴权再返回」的响应。带 public 出去，中间任何
    # 共享缓存/CDN 都可能把这一份发给下一个没带令牌的请求，等于绕过上面的
    # 归属校验，所以这里降级成 private + no-store。
    cache_control = "private, max-age=0, no-store" if is_private_object_name(object_name) else CACHE_CONTROL
    return {
        "Cache-Control": cache_control,
        "ETag": f'"{safe_etag}"',
        # 存的是用户上传的字节，类型只按上传时声明的 content_type 回放；
        # 禁掉浏览器嗅探，避免「声明 image/png 的 HTML」被当页面执行。
        "X-Content-Type-Options": "nosniff",
    }


def get_minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def get_aliyun_oss_bucket():
    if not settings.aliyun_oss_endpoint or not settings.aliyun_oss_bucket:
        raise AppException("未配置阿里云 OSS Endpoint 或 Bucket", 503)
    if not settings.aliyun_oss_access_key_id or not settings.aliyun_oss_access_key_secret:
        raise AppException("未配置阿里云 OSS AccessKey", 503)
    try:
        import oss2
    except ImportError as exc:
        raise AppException("未安装阿里云 OSS SDK，请执行 pip install oss2", 503) from exc
    auth = oss2.Auth(settings.aliyun_oss_access_key_id, settings.aliyun_oss_access_key_secret)
    endpoint = settings.aliyun_oss_endpoint.strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"{'https' if settings.aliyun_oss_secure else 'http'}://{endpoint}"
    return oss2.Bucket(auth, endpoint, settings.aliyun_oss_bucket)


def _upload_minio(object_name: str, content: bytes, content_type: str) -> None:
    client = get_minio_client()
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)
    client.put_object(
        settings.minio_bucket,
        object_name,
        BytesIO(content),
        length=len(content),
        content_type=content_type,
        metadata={"Cache-Control": CACHE_CONTROL},
    )


def _upload_aliyun_oss(object_name: str, content: bytes, content_type: str) -> None:
    bucket = get_aliyun_oss_bucket()
    result = bucket.put_object(object_name, content, headers={"Content-Type": content_type, "Cache-Control": CACHE_CONTROL})
    status = getattr(result, "status", 0)
    if status < 200 or status >= 300:
        raise AppException(f"阿里云 OSS 上传失败，状态码：{status}", 502)


def read_upload_within_limit(file: UploadFile, message: str) -> bytes:
    """分块读取上传体，累计超过 MAX_SIZE 立刻拒绝。

    顺序必须是「边读边判」，不能「先全读进内存再判长度」：后者等于让上传者
    决定进程要分配多少内存——直连 8000（没有 Nginx 的 client_max_body_size）
    时一个几百 MB 的请求就能把容器打到 OOM。分块后峰值内存是 MAX_SIZE 加
    一个块，与请求体大小无关。

    先看一眼 ``size``（Starlette 从 multipart 的 Content-Length 填）是快路径，
    免得为必然被拒的请求真的读满 5MB；它可能为 None，所以不能只靠它。
    """
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > MAX_SIZE:
        raise AppException(message, 400)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_SIZE:
            raise AppException(message, 400)
        chunks.append(chunk)
    return b"".join(chunks)


def upload_avatar(db: Session, user_id: int, file: UploadFile) -> dict[str, str]:
    if file.content_type not in ALLOWED_TYPES:
        raise AppException("头像仅支持 jpg、jpeg、png、webp", 400)
    content = read_upload_within_limit(file, f"头像不能超过 {MAX_SIZE_MB}MB")
    existing = (
        db.query(UploadedFile)
        .filter(UploadedFile.user_id == user_id, UploadedFile.file_type == "avatar")
        .count()
    )
    if existing >= MAX_AVATARS_PER_USER:
        raise AppException(f"头像最多保留 {MAX_AVATARS_PER_USER} 张，请先到信息库删除不用的头像", 400)

    suffix = ALLOWED_TYPES[file.content_type]
    object_name = f"avatar/{user_id}_{uuid4().hex}{suffix}"

    if _provider() == "aliyun_oss":
        _upload_aliyun_oss(object_name, content, file.content_type)
    else:
        _upload_minio(object_name, content, file.content_type)

    url = _file_url(object_name)
    db.add(
        UploadedFile(
            user_id=user_id,
            file_type="avatar",
            file_name=file.filename or object_name,
            object_name=object_name,
            file_url=url,
            content_type=file.content_type,
            file_size=len(content),
        )
    )
    db.commit()
    # 返回给浏览器的是签名 URL：前端不再需要把登录 token 拼进图片地址。
    return {"url": sign_private_url(url, object_name), "object_name": object_name, "provider": _provider()}


def upload_announcement_image(db: Session, user_id: int, file: UploadFile) -> dict[str, str]:
    if file.content_type not in ALLOWED_TYPES:
        raise AppException("公告图片仅支持 jpg、jpeg、png、webp", 400)
    content = read_upload_within_limit(file, f"公告图片不能超过 {MAX_SIZE_MB}MB")

    suffix = ALLOWED_TYPES[file.content_type]
    object_name = f"announcement/{uuid4().hex}{suffix}"
    if _provider() == "aliyun_oss":
        _upload_aliyun_oss(object_name, content, file.content_type)
    else:
        _upload_minio(object_name, content, file.content_type)

    url = _file_url(object_name)
    db.add(
        UploadedFile(
            user_id=user_id,
            file_type="announcement_image",
            file_name=file.filename or object_name,
            object_name=object_name,
            file_url=url,
            content_type=file.content_type,
            file_size=len(content),
        )
    )
    db.commit()
    return {"url": url, "object_name": object_name, "provider": _provider()}


def upload_ai_chat_image(db: Session, user_id: int, file: UploadFile) -> dict[str, str]:
    if file.content_type not in ALLOWED_TYPES:
        raise AppException("AI 对话图片仅支持 jpg、jpeg、png、webp", 400)
    content = read_upload_within_limit(file, f"AI 对话图片不能超过 {MAX_SIZE_MB}MB")

    suffix = ALLOWED_TYPES[file.content_type]
    object_name = f"ai-chat/{user_id}_{uuid4().hex}{suffix}"
    if _provider() == "aliyun_oss":
        _upload_aliyun_oss(object_name, content, file.content_type)
    else:
        _upload_minio(object_name, content, file.content_type)

    url = _file_url(object_name)
    db.add(
        UploadedFile(
            user_id=user_id,
            file_type="ai_chat_image",
            file_name=file.filename or object_name,
            object_name=object_name,
            file_url=url,
            content_type=file.content_type,
            file_size=len(content),
        )
    )
    db.commit()
    return {"url": url, "object_name": object_name, "provider": _provider()}


def sign_private_url(url: str, object_name: str) -> str:
    """给私有对象 URL 补上短签名；非私有对象原样返回。

    序列化路径（上传响应、历史消息）都过这里，所以浏览器拿到的永远是「只对这张图、
    几小时内有效」的凭据，而不是一整个登录态。
    """
    if not url or not is_private_object_name(object_name):
        return url
    return append_signature(url, object_name)


def _iter_oss_object(obj) -> Iterator[bytes]:
    try:
        while True:
            chunk = obj.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        close = getattr(obj, "close", None)
        if callable(close):
            close()


def stream_uploaded_file(object_name: str) -> StreamingResponse:
    # 对象名来自 URL 路径，必须先过业务前缀白名单：否则可以拿任意桶内 key
    # （包括其他模块写的对象）走这个公开代理。
    if not is_allowed_object_name(object_name):
        raise AppException("文件不存在", 404)
    if _provider() == "aliyun_oss":
        obj = get_aliyun_oss_bucket().get_object(object_name)
        content_type = getattr(obj, "headers", {}).get("Content-Type", "application/octet-stream")
        return StreamingResponse(_iter_oss_object(obj), media_type=content_type, headers=_stream_cache_headers(object_name))

    response = get_minio_client().get_object(settings.minio_bucket, object_name)
    return StreamingResponse(
        response.stream(CHUNK_SIZE),
        media_type=response.headers.get("Content-Type", "application/octet-stream"),
        headers=_stream_cache_headers(object_name),
    )


def read_uploaded_file(object_name: str) -> tuple[bytes, str]:
    if _provider() == "aliyun_oss":
        obj = get_aliyun_oss_bucket().get_object(object_name)
        try:
            content = obj.read()
            content_type = getattr(obj, "headers", {}).get("Content-Type", "application/octet-stream")
            return content, content_type
        finally:
            close = getattr(obj, "close", None)
            if callable(close):
                close()

    response = get_minio_client().get_object(settings.minio_bucket, object_name)
    try:
        return response.read(), response.headers.get("Content-Type", "application/octet-stream")
    finally:
        response.close()
        response.release_conn()


# ---- 运维清扫用：列举与删除（不在请求路径上，只由 maintenance_service 调用）----

def list_object_names(prefix: str, *, before) -> list[str]:
    """列出前缀下「最后修改早于 before」的对象名。

    仅供清扫器使用：整桶列举的成本与对象总量线性相关，绝不能出现在请求路径里。
    任何存储侧异常都按「没有对象」处理——清扫失败不该比漏扫更糟。
    """
    names: list[str] = []
    if prefix not in ALLOWED_OBJECT_PREFIXES:
        return names
    try:
        if _provider() == "aliyun_oss":
            bucket = get_aliyun_oss_bucket()
            for item in bucket.list_objects(prefix=prefix, max_keys=1000).object_list or []:
                if item.last_modified < before:
                    names.append(item.key)
            return names
        client = get_minio_client()
        for item in client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True):
            # MinIO 返回的 last_modified 是 UTC aware，比较前统一去掉时区。
            modified = item.last_modified
            if modified is not None and modified.replace(tzinfo=None) < before:
                names.append(item.object_name)
    except Exception:  # noqa: BLE001 - 清扫是尽力而为
        logger.warning("列举存储对象失败：%s", prefix, exc_info=True)
    return names


def delete_object_names(names: list[str]) -> int:
    """按对象名删除，返回成功条数。先过业务前缀白名单。"""
    deleted = 0
    provider = _provider()
    for name in names:
        if not is_allowed_object_name(name):
            continue
        try:
            if provider == "aliyun_oss":
                get_aliyun_oss_bucket().delete_object(name)
            else:
                get_minio_client().remove_object(settings.minio_bucket, name)
            deleted += 1
        except Exception:  # noqa: BLE001 - 单个对象失败不影响其余
            logger.warning("删除存储对象失败：%s", name, exc_info=True)
    return deleted
