from typing import Any

from fastapi.responses import JSONResponse


def success(data: Any = None, message: str = "success") -> dict[str, Any]:
    return {"code": 200, "message": message, "data": data}


def error_response(message: str, code: int = 500, status_code: int = 200, data: Any = None) -> JSONResponse:
    """错误也允许带结构化 `data`。

    典型场景是 409 乐观锁冲突：前端需要拿到服务端的最新版本号才能给出
    「覆盖 / 载入最新版」两个出口；只回一句 message 等于让客户端再猜一次。
    """
    return JSONResponse(status_code=status_code, content={"code": code, "message": message, "data": data})
