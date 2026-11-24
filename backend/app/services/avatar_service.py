"""头像库。

头像不是简历内容，而是账号级素材：同一张照片会被多份简历复用，也会在换一份
简历时被重新挑选。因此这里不再单独建表，直接用 ``uploaded_files`` 里
``file_type="avatar"`` 的记录当库——上传接口本来就会写这一行，用户每传一张就
自动入库，天然按 ``user_id`` 隔离。

简历侧只保存**对象名**（``avatar/1_xxx.jpg``），不保存完整 URL：库里换域名、
换部署都不该让简历里的照片失效。历史数据里存着旧版完整 URL 的，读侧继续兼容
（见 :func:`avatar_object_name`），不需要数据迁移。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.resume import Resume, UploadedFile
from app.services.storage.storage_service import (
    AVATAR_OBJECT_PREFIX,
    MAX_AVATARS_PER_USER,
    is_allowed_object_name,
)


def avatar_object_name(value: Any) -> str:
    """从简历里存的头像值还原出对象名。

    三种形态都认：对象名（新）、``/api/files/`` 代理 URL（旧）、MinIO/OSS 公共
    地址（配了 ``STORAGE_PUBLIC_URL_MODE=public`` 的环境）。认不出来（外链、
    data URI、空值）就返回空串，让调用方保留原值走外部资源校验。
    """
    text = str(value or "").strip()
    if not text or text.startswith("data:"):
        return ""
    if text.startswith(AVATAR_OBJECT_PREFIX):
        return text if is_allowed_object_name(text) else ""
    candidate = ""
    marker = "/api/files/"
    if marker in text:
        candidate = text.split(marker, 1)[1].split("?", 1)[0]
    else:
        for base in (settings.minio_public_url, settings.aliyun_oss_public_url):
            base_url = (base or "").strip().rstrip("/")
            if base_url and text.startswith(f"{base_url}/"):
                candidate = text[len(base_url) + 1 :].split("?", 1)[0]
                break
    if candidate and candidate.startswith(AVATAR_OBJECT_PREFIX) and is_allowed_object_name(candidate):
        return candidate
    return ""


def render_url(object_name: str) -> str:
    """后端渲染（Chromium / PDF）用的绝对地址：模板里的图片必须能被渲染进程取到。"""
    return f"{settings.pdf_base_url.rstrip('/')}/api/files/{object_name}"


def browser_url(object_name: str) -> str:
    """前端展示用的相对地址：开发走 Vite 代理、生产走同源 Nginx，都不绑主机名。"""
    return f"/api/files/{object_name}"


def resolve_for_render(value: Any, *, absolute: bool = True) -> str:
    """把简历里的头像值解析成可渲染的地址；认不出来就原样返回。

    ``absolute=True`` 给服务器端渲染（Chromium / WeasyPrint）用：渲染进程没有
    页面上下文，必须给能直接请求的绝对地址。``absolute=False`` 给浏览器用：
    预览与分享页都以 ``iframe srcdoc`` 交给浏览器，相对地址会跟随页面源站，
    既不会被写死的 ``pdf_base_url`` 带到 127.0.0.1，换域名也不用改配置。
    """
    object_name = avatar_object_name(value)
    if not object_name:
        return str(value or "").strip()
    return render_url(object_name) if absolute else browser_url(object_name)


def _avatar_query(db: Session, user_id: int):
    return db.query(UploadedFile).filter(
        UploadedFile.user_id == user_id,
        UploadedFile.file_type == "avatar",
    )


def count_avatars(db: Session, user_id: int) -> int:
    return _avatar_query(db, user_id).count()


def usage_counts(db: Session, user_id: int) -> dict[str, int]:
    """对象名 → 有多少份简历正在用它。

    删除前要让用户知道「这张还被 2 份简历用着」，否则删完那些简历会裂图。
    """
    counts: dict[str, int] = {}
    rows = db.query(Resume.resume_data).filter(Resume.user_id == user_id).all()
    for (data,) in rows:
        if not isinstance(data, dict):
            continue
        basics = data.get("basics")
        if not isinstance(basics, dict):
            continue
        object_name = avatar_object_name(basics.get("avatar"))
        if object_name:
            counts[object_name] = counts.get(object_name, 0) + 1
    return counts


def avatar_out(row: UploadedFile, usage: Optional[dict[str, int]] = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "object_name": row.object_name,
        "url": browser_url(row.object_name),
        "file_name": row.file_name,
        "content_type": row.content_type,
        "file_size": row.file_size,
        "usage_count": (usage or {}).get(row.object_name, 0),
        "create_time": row.create_time.isoformat() if row.create_time else "",
    }


def list_avatars(db: Session, user_id: int) -> dict[str, Any]:
    rows = _avatar_query(db, user_id).order_by(UploadedFile.create_time.desc(), UploadedFile.id.desc()).all()
    usage = usage_counts(db, user_id)
    return {
        "items": [avatar_out(row, usage) for row in rows],
        "total": len(rows),
        "limit": MAX_AVATARS_PER_USER,
    }


def latest_object_name(db: Session, user_id: int) -> str:
    """最近上传的一张，用于「新建简历时先带上默认头像」这类需要兜底的场景。"""
    row = _avatar_query(db, user_id).order_by(UploadedFile.create_time.desc(), UploadedFile.id.desc()).first()
    return row.object_name if row else ""


def delete_avatar(db: Session, user_id: int, avatar_id: int) -> None:
    """从库里移除一张头像。

    只删索引、**不删存储对象**：已分享或已导出的简历仍引用着这张图，删对象会让
    它们当场裂图；而对象名带 uuid，留着不会被别处误用。真要做空间回收，应该走
    离线清理任务，而不是跟着一次点击删文件。
    """
    row = _avatar_query(db, user_id).filter(UploadedFile.id == avatar_id).first()
    if row is None:
        return
    db.delete(row)
    db.commit()
