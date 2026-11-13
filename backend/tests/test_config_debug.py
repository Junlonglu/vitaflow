"""``APP_DEBUG`` 的生效规则：生产/预发一律按关闭处理。

FastAPI 的 ``debug=True`` 最终落在 Starlette 的 ServerErrorMiddleware 上：未捕获
异常直接回 traceback HTML，``@app.exception_handler(Exception)`` 根本不会被调用。
所以「能不能打开调试」不能只看配置项，必须由 ``Settings.debug_enabled`` 按环境
兜一道，否则生产上一旦有人把 ``APP_DEBUG`` 留成 true，调用栈和源码片段就会发给
前端。
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.exceptions as exceptions_module
from app.core.config import Settings
from app.core.exceptions import register_exception_handlers


SECRET_DETAIL = "SELECT * FROM user_password_reset_token"


def _client(*, debug: bool) -> TestClient:
    app = FastAPI(debug=debug)
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError(SECRET_DETAIL)

    return TestClient(app, raise_server_exceptions=False)


def test_debug_field_defaults_to_off():
    # 不读 .env，只验字段默认值：默认必须是关闭的。
    assert Settings(_env_file=None).app_debug is False


def test_production_envs_force_debug_off():
    for env in ("production", "prod", "staging", "  PRODUCTION  "):
        config = Settings(_env_file=None, app_env=env, app_debug=True)
        assert config.is_production is True
        assert config.debug_enabled is False, env


def test_development_keeps_explicit_switch():
    assert Settings(_env_file=None, app_env="development", app_debug=True).debug_enabled is True
    assert Settings(_env_file=None, app_env="development", app_debug=False).debug_enabled is False


def test_unhandled_exception_is_masked_when_debug_off(monkeypatch):
    monkeypatch.setattr(exceptions_module, "settings", SimpleNamespace(debug_enabled=False))

    response = _client(debug=False).get("/boom")

    assert response.status_code == 500
    assert response.json()["code"] == 500
    assert response.json()["message"] == "服务器开小差了，请稍后重试"
    assert SECRET_DETAIL not in response.text


def test_unhandled_exception_is_echoed_when_debug_on(monkeypatch):
    monkeypatch.setattr(exceptions_module, "settings", SimpleNamespace(debug_enabled=True))

    response = _client(debug=False).get("/boom")

    assert response.status_code == 500
    assert SECRET_DETAIL in response.json()["message"]


def test_starlette_debug_bypasses_the_exception_handler():
    """记录改动的理由：debug=True 时异常处理器根本没机会执行。

    这条断言的是 Starlette 自身行为（不是我们的代码），一旦哪天它不再回显
    traceback，说明「生产关 debug」这条硬性要求的前提变了，需要重新评估。
    """
    response = _client(debug=True).get("/boom")

    assert response.status_code == 500
    assert SECRET_DETAIL in response.text
    assert "服务器开小差了" not in response.text
