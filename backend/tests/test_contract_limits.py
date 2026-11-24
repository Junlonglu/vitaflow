"""跨层上限与启动守卫。

两处问题都出在「同一个值/同一条规则在多处各写一遍」：

- **JWT 密钥**：``validate_encryption_config`` 只在「没配独立 BYOK 密钥」时才顺带
  看一眼 JWT，配了 BYOK 就静默放行弱登录密钥——任何拿到示例默认值的人都能伪造
  任意用户。所以登录态必须独立校验，且生产直接拒绝启动。
- **标题长度**：列宽 100、校验 120、复制时截 120，于是复制一份长标题简历必然
  撞 ``Data too long``（500）。现在四处共用 ``RESUME_TITLE_MAX``。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.core.security as security_module
import app.services.resume_service as resume_service
from app.core.config import DEFAULT_JWT_SECRET_KEY
from app.core.contracts import RESUME_TITLE_MAX
from app.core.security import InsecureJwtSecretError, validate_jwt_secret
from app.models.resume import Resume
from app.schemas.resume import ResumeCreate, ResumeDuplicateRequest, ResumeUpdate


def _guarded(monkeypatch, secret: str) -> str:
    monkeypatch.setattr(security_module, "settings", SimpleNamespace(jwt_secret_key=secret))
    return validate_jwt_secret(strict=False)


def test_default_secret_is_reported(monkeypatch):
    message = _guarded(monkeypatch, DEFAULT_JWT_SECRET_KEY)
    assert "默认值" in message
    assert "伪造" in message


def test_default_secret_refuses_to_start_in_production(monkeypatch):
    monkeypatch.setattr(
        security_module, "settings", SimpleNamespace(jwt_secret_key=DEFAULT_JWT_SECRET_KEY)
    )
    with pytest.raises(InsecureJwtSecretError):
        validate_jwt_secret(strict=True)


def test_short_or_empty_secret_is_reported(monkeypatch):
    assert "字节" in _guarded(monkeypatch, "short-secret")
    assert "为空" in _guarded(monkeypatch, "   ")


def test_strong_secret_passes(monkeypatch):
    strong = "6Ld1s0m3-r4nd0m-s3cr3t-value-with-48-bytes!!"
    assert len(strong.encode()) >= 32
    assert _guarded(monkeypatch, strong) == ""
    monkeypatch.setattr(security_module, "settings", SimpleNamespace(jwt_secret_key=strong))
    assert validate_jwt_secret(strict=True) == ""


def test_title_limit_is_the_same_everywhere():
    assert RESUME_TITLE_MAX == 100
    # 列宽、校验、截断引用同一个常量，任一改动都会被这条断言拽回来。
    assert Resume.__table__.c.title.type.length == RESUME_TITLE_MAX


@pytest.mark.parametrize("model", [ResumeCreate, ResumeUpdate, ResumeDuplicateRequest])
def test_over_long_title_is_rejected_before_hitting_the_database(model):
    with pytest.raises(ValidationError):
        model(title="长" * (RESUME_TITLE_MAX + 1))
    # 正好 100 字必须能过：历史数据里有 100 字标题，不能把它们判成非法。
    model(title="长" * RESUME_TITLE_MAX)


def test_duplicate_keeps_the_suffix_and_stays_within_limit(monkeypatch):
    """源标题已经顶到上限时，「XXX 副本」不能再超长，也不能把后缀裁掉。

    这里是服务层逻辑测试：SQLite 上 BigInteger 主键不自增，副本行没有显式 id
    插不进去，所以用假 Resume 接管构造参数，只校验标题计算。
    """
    source = SimpleNamespace(
        id=1,
        user_id=1,
        title="长" * RESUME_TITLE_MAX,
        language="zh-CN",
        resume_data={},
        template_id="tech",
        template_config={},
    )
    captured: dict[str, object] = {}

    class _FakeResume:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    db = SimpleNamespace(add=lambda obj: None, commit=lambda: None, refresh=lambda obj: None)
    monkeypatch.setattr(resume_service, "get_resume", lambda *_args: source)
    monkeypatch.setattr(resume_service, "Resume", _FakeResume)

    resume_service.duplicate_resume(db, 1, 1)

    title = str(captured["title"])
    assert title.endswith("副本")
    assert len(title) <= RESUME_TITLE_MAX


def test_explicit_copy_title_is_respected(monkeypatch):
    source = SimpleNamespace(
        id=1, user_id=1, title="原简历", language="zh-CN",
        resume_data={}, template_id="tech", template_config={},
    )
    captured: dict[str, object] = {}

    class _FakeResume:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    db = SimpleNamespace(add=lambda obj: None, commit=lambda: None, refresh=lambda obj: None)
    monkeypatch.setattr(resume_service, "get_resume", lambda *_args: source)
    monkeypatch.setattr(resume_service, "Resume", _FakeResume)

    resume_service.duplicate_resume(db, 1, 1, "定制的名字")

    assert captured["title"] == "定制的名字"
