import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.response import error_response


logger = logging.getLogger(__name__)

FIELD_NAMES = {
    "email": "邮箱",
    "password": "密码",
    "current_password": "当前密码",
    "new_password": "新密码",
    "username": "用户名",
    "verification_code": "验证码",
}


def friendly_validation_message(exc: RequestValidationError) -> str:
    error = exc.errors()[0]
    field = str(error.get("loc", [""])[-1])
    error_type = str(error.get("type", ""))
    label = FIELD_NAMES.get(field, "提交内容")

    if field == "email":
        return "请输入正确的邮箱地址"
    if field == "verification_code":
        return "请输入 6 位邮箱验证码"
    if error_type == "missing":
        return f"请填写{label}"
    if error_type in {"string_too_short", "string_too_long"}:
        context = error.get("ctx") or {}
        limit = context.get("min_length") or context.get("max_length")
        direction = "至少" if error_type == "string_too_short" else "最多"
        return f"{label}{direction}需要 {limit} 个字符"
    return f"{label}格式不正确"


class AppException(Exception):
    """业务异常。

    `code` 默认 500，表示「约定之外的意外」。资源查找失败请显式传 404，
    参数不合法传 400，状态冲突传 409 —— 否则前端和其它服务调用方只能看到
    「服务端炸了」，无法区分是数据不存在还是真的出错。
    """

    def __init__(self, message: str, code: int = 500, data: dict | None = None):
        self.message = message
        self.code = code
        # 少数冲突类错误需要把「服务端现在的样子」告诉调用方（如 409 的
        # latest_revision），否则前端只能再发一次请求去猜。
        self.data = data


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(_: Request, exc: AppException):
        status_code = exc.code if 400 <= exc.code <= 599 else 200
        return error_response(exc.message, exc.code, status_code, exc.data)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException):
        return error_response(str(exc.detail), exc.status_code, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError):
        return error_response(friendly_validation_message(exc), 422, 422)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        # 细节只进日志：`str(exc)` 里可能是 SQL 语句、文件路径、驱动返回码，
        # 直接回给前端等于把内部结构送出去。调试环境保留原文，方便本地定位；
        # settings.debug_enabled 已在生产/预发上强制为 False。
        logger.exception("Unhandled exception while processing %s %s", request.method, request.url.path)
        if settings.debug_enabled:
            return error_response(str(exc) or exc.__class__.__name__, 500, 500)
        return error_response("服务器开小差了，请稍后重试", 500, 500)
