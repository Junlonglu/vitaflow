import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin,
    ai,
    announcements,
    auth,
    avatars,
    exports,
    feedback,
    files,
    info_fragments,
    resume_starters,
    resumes,
    shares,
    templates,
    user_ai,
)
from app.core.config import settings
from app.core.crypto import ByokKeyError, validate_encryption_config
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.response import success
from app.core.security import InsecureJwtSecretError, validate_jwt_secret
from app.core.startup_checks import StartupContractError, run_startup_checks
from app.services.maintenance_service import sweeper


configure_logging()

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("vitaflow.access")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "Starting %s backend env=%s debug=%s",
        settings.app_name,
        settings.app_env,
        settings.debug_enabled,
    )
    try:
        jwt_warning = validate_jwt_secret(strict=settings.is_production)
        if jwt_warning:
            logger.warning("JWT 配置不安全：%s", jwt_warning)
    except InsecureJwtSecretError as exc:
        logger.error("JWT 配置不安全，拒绝启动：%s", exc)
        raise
    try:
        warning = validate_encryption_config(strict=settings.is_production)
        if warning:
            logger.warning("BYOK 加密配置：%s", warning)
    except ByokKeyError as exc:
        logger.error("BYOK 加密配置校验失败，拒绝启动：%s", exc)
        raise
    # 组合约束：把「各自合法、凑一起就坏」的配置挡在启动之前。
    # 超时链 / 请求体预算 / 连接池 / 渲染基址 / Node 侧契约都在这一个入口核对，
    # 问题一次性列全，而不是第一个失败就把后面的遮住。
    try:
        await run_startup_checks()
    except StartupContractError as exc:
        logger.error("%s", exc)
        raise
    # 生命周期清扫：非终态都有租约，到期由它收尾（超时任务退款、过期提案失效、
    # 孤儿回合补账）。SIGTERM 时在 finally 里等当前一轮跑完再退出。
    sweeper.start()
    try:
        yield
    finally:
        await sweeper.stop()
        logger.info("Stopping %s backend", settings.app_name)


app = FastAPI(title=settings.app_name, debug=settings.debug_enabled, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    if not settings.log_access_enabled:
        return await call_next(request)

    start_time = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        status_code = response.status_code if response else 500
        client_host = request.client.host if request.client else "-"
        access_logger.info(
            '%s %s "%s %s" %s %.2fms',
            client_host,
            request.headers.get("x-forwarded-for", "-"),
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )


app.include_router(auth.router, prefix="/api")
app.include_router(resumes.router, prefix="/api")
app.include_router(resume_starters.router, prefix="/api")
app.include_router(shares.router, prefix="/api")
app.include_router(templates.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(avatars.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(announcements.router, prefix="/api")
app.include_router(user_ai.router, prefix="/api")
app.include_router(info_fragments.router, prefix="/api")


@app.get("/")
def root():
    return success({"name": settings.app_name, "cn_name": settings.app_cn_name})
