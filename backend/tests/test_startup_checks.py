"""启动自检：配置组合错了要拒绝启动，而且要一次说清全部问题。

这些用例全部不联网、不起服务：``static_problems`` 是纯函数，跨服务那段用
假的 httpx 客户端顶上。真正要守住的行为有两条：

1. **在生产拒绝启动**：组合错误在生产是确定性故障，不是「可能有点慢」；
2. **一次列全**：只报第一条会让人改一版启一版，而这些问题通常是一起出现的。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from types import SimpleNamespace

from app.core import contracts, startup_checks
from app.core.config import Settings
from app.core.startup_checks import (
    StartupContractError,
    ai_service_problems,
    run_startup_checks,
    static_problems,
)


def _settings(**overrides) -> Settings:
    """一份自洽的配置，只改用例关心的那几项。"""
    base = dict(
        app_env="production",
        db_pool_size=contracts.DB_POOL_SIZE,
        db_max_overflow=contracts.DB_MAX_OVERFLOW,
        db_pool_timeout=contracts.DB_POOL_TIMEOUT_SECONDS,
        ai_turn_timeout_ms=contracts.AI_TURN_TIMEOUT_MS,
        ai_proxy_read_timeout_ms=contracts.AI_PROXY_READ_TIMEOUT_MS,
        nginx_proxy_read_timeout_ms=contracts.NGINX_PROXY_READ_TIMEOUT_MS,
        pdf_base_url="http://127.0.0.1:8000",
        ai_service_url="http://ai-service:8300",
        ai_service_token="test-token",
    )
    base.update(overrides)
    return Settings(**base)


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """替身 AsyncClient：把「远端说了什么」变成一次显式注入。"""

    response: _FakeResponse | None = None
    error: Exception | None = None
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def get(self, url, headers=None):
        _FakeClient.calls.append({"url": url, "headers": headers or {}})
        if _FakeClient.error is not None:
            raise _FakeClient.error
        return _FakeClient.response or _FakeResponse()


@pytest.fixture
def fake_httpx(monkeypatch):
    _FakeClient.error = None
    _FakeClient.response = _FakeResponse()
    _FakeClient.calls = []
    monkeypatch.setattr(
        startup_checks,
        "httpx",
        SimpleNamespace(AsyncClient=_FakeClient, HTTPError=httpx.HTTPError),
    )
    return _FakeClient


# ---------------------------------------------------------------------------
# 静态检查
# ---------------------------------------------------------------------------


def test_sane_config_has_no_static_problems():
    assert static_problems(_settings()) == []


def test_proxy_read_timeout_shorter_than_turn_is_rejected():
    """代理比回合先到期，用户只会看到「没有结束原因的断流」（RF-033）。"""
    problems = static_problems(_settings(ai_proxy_read_timeout_ms=180_000))
    assert len(problems) == 1
    assert "掐断" in problems[0]


def test_nginx_timeout_must_be_the_longest():
    problems = static_problems(
        _settings(ai_proxy_read_timeout_ms=330_000, nginx_proxy_read_timeout_ms=340_000)
    )
    assert any("Nginx" in item for item in problems)


def test_pool_timeout_default_of_30s_is_rejected():
    problems = static_problems(_settings(db_pool_timeout=30))
    assert any("DB_POOL_TIMEOUT" in item for item in problems)


def test_relative_pdf_base_url_is_rejected():
    problems = static_problems(_settings(pdf_base_url="/api/files"))
    assert any("PDF_BASE_URL" in item for item in problems)


def test_loopback_pdf_base_url_is_allowed():
    """Chromium 与后端同容器时回环地址是正确的，不能因为「生产」就拦。"""
    assert static_problems(_settings(pdf_base_url="http://127.0.0.1:8000")) == []


def test_all_static_problems_are_reported_together():
    """一次列全：修一条、启一次、再撞下一条，是最浪费时间的循环。"""
    problems = static_problems(
        _settings(
            ai_proxy_read_timeout_ms=60_000,
            db_pool_timeout=30,
            pdf_base_url="not-a-url",
        )
    )
    assert len(problems) >= 3


# ---------------------------------------------------------------------------
# 跨服务检查
# ---------------------------------------------------------------------------


def test_unreachable_ai_service_is_a_warning_not_a_failure(fake_httpx):
    fake_httpx.error = httpx.ConnectError("connection refused")
    problems, unreachable = asyncio.run(ai_service_problems(_settings()))
    assert problems == []
    assert "连不上" in unreachable
    # 探活必须带令牌：/v1/contracts 不是公开端点。
    assert fake_httpx.calls[0]["headers"]["x-ai-service-token"] == "test-token"


def test_ai_service_longer_turn_than_proxy_is_rejected(fake_httpx):
    fake_httpx.response = _FakeResponse(
        200,
        {
            "turn_timeout_ms": 600_000,
            "body_limit_bytes": contracts.NODE_BODY_MAX_BYTES,
            "provider": "real",
        },
    )
    problems, unreachable = asyncio.run(ai_service_problems(_settings()))
    assert unreachable == ""
    assert any("掐断" in item for item in problems)


def test_ai_service_small_body_limit_is_rejected(fake_httpx):
    """Node 只收 8MB 时，一张 5MB 的图 base64 后都装不下（RF-044）。"""
    fake_httpx.response = _FakeResponse(
        200,
        {
            "turn_timeout_ms": contracts.AI_TURN_TIMEOUT_MS,
            "body_limit_bytes": 8 * 1024 * 1024,
            "provider": "real",
        },
    )
    problems, _ = asyncio.run(ai_service_problems(_settings()))
    assert any("请求体上限不够" in item for item in problems)


def test_faux_provider_is_rejected_in_production(fake_httpx):
    fake_httpx.response = _FakeResponse(
        200,
        {
            "turn_timeout_ms": contracts.AI_TURN_TIMEOUT_MS,
            "body_limit_bytes": contracts.NODE_BODY_MAX_BYTES,
            "provider": "faux",
        },
    )
    problems, _ = asyncio.run(ai_service_problems(_settings()))
    assert any("faux" in item for item in problems)


def test_faux_provider_is_tolerated_outside_production(fake_httpx):
    fake_httpx.response = _FakeResponse(
        200,
        {
            "turn_timeout_ms": contracts.AI_TURN_TIMEOUT_MS,
            "body_limit_bytes": contracts.NODE_BODY_MAX_BYTES,
            "provider": "faux",
        },
    )
    problems, _ = asyncio.run(ai_service_problems(_settings(app_env="development")))
    assert problems == []


# ---------------------------------------------------------------------------
# 编排：什么时候抛
# ---------------------------------------------------------------------------


def test_production_refuses_to_start(fake_httpx):
    fake_httpx.error = httpx.ConnectError("connection refused")
    with pytest.raises(StartupContractError) as excinfo:
        asyncio.run(run_startup_checks(_settings(db_pool_timeout=30)))
    assert "DB_POOL_TIMEOUT" in str(excinfo.value)


def test_development_only_warns(fake_httpx):
    fake_httpx.error = httpx.ConnectError("connection refused")
    # 本地常见「池参数还没配」，不该把开发环境挡在门外。
    asyncio.run(run_startup_checks(_settings(app_env="development", db_pool_timeout=30)))


def test_remote_mismatch_fails_even_in_development(fake_httpx):
    """跨服务不一致是确定性故障，开发环境也不该带病运行。"""
    fake_httpx.response = _FakeResponse(
        200,
        {
            "turn_timeout_ms": 600_000,
            "body_limit_bytes": contracts.NODE_BODY_MAX_BYTES,
            "provider": "faux",
        },
    )
    with pytest.raises(StartupContractError):
        asyncio.run(run_startup_checks(_settings(app_env="development")))


def test_token_rejection_is_reported_as_such(fake_httpx):
    """401 和「连不上」不是一回事：前者是令牌不一致，后者是没起来。"""
    fake_httpx.response = _FakeResponse(401, {"message": "unauthorized"})
    problems, unreachable = asyncio.run(ai_service_problems(_settings()))
    assert problems == []
    assert "令牌" in unreachable


def test_same_problem_from_both_tiers_is_reported_once(fake_httpx):
    """静态档与远端档会各算一次超时链；配置一致时结论相同，不该报两条。"""
    fake_httpx.response = _FakeResponse(
        200,
        {
            "turn_timeout_ms": contracts.AI_TURN_TIMEOUT_MS,
            "body_limit_bytes": contracts.NODE_BODY_MAX_BYTES,
            "provider": "real",
        },
    )
    with pytest.raises(StartupContractError) as excinfo:
        asyncio.run(run_startup_checks(_settings(ai_proxy_read_timeout_ms=100_000)))

    message = str(excinfo.value)
    assert "共 1 项" in message
    assert message.count("掐断") == 1


def test_public_storage_mode_is_refused_in_production():
    """对话图片走对象存储公开地址 = 绕过登录与签名，生产环境不该带病启动。"""
    problems = static_problems(_settings(app_env="production", storage_public_url_mode="public"))
    assert any("STORAGE_PUBLIC_URL_MODE" in item for item in problems)


def test_public_storage_mode_only_warns_in_development():
    problems = static_problems(_settings(app_env="development", storage_public_url_mode="public"))
    assert not any("STORAGE_PUBLIC_URL_MODE" in item for item in problems)
