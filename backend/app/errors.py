"""统一错误码与响应包装。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


# 业务错误码：前端按 code 分支处理，避免依赖文案
E_OK = 0
E_BAD_REQUEST = 40001
E_UNAUTHORIZED = 40101
E_FORBIDDEN = 40301
E_NOT_FOUND = 40401
E_CONFLICT = 40901
E_LIMITED = 42901
E_UPSTREAM = 50201
E_DAILY_BUDGET = 42902
E_INTERNAL = 50001

MESSAGES = {
    E_OK: "ok",
    E_BAD_REQUEST: "提交内容有误，请检查后重试",
    E_UNAUTHORIZED: "请先登录",
    E_FORBIDDEN: "当前账号无权进行此操作",
    E_NOT_FOUND: "没有找到相关内容",
    E_CONFLICT: "内容已发生变化，请刷新后重试",
    E_LIMITED: "操作过于频繁，请稍后再试",
    E_DAILY_BUDGET: "今日生成服务已达上限，请明天再试",
    E_UPSTREAM: "生成服务暂时不可用，请稍后重试",
    E_INTERNAL: "服务暂时不可用，请稍后重试",
}


def ok(data: Any = None, message: str = "ok") -> Dict[str, Any]:
    return {"code": E_OK, "message": message, "data": data}


def fail(code: int, message: Optional[str] = None, data: Any = None) -> Dict[str, Any]:
    return {"code": code, "message": message or MESSAGES.get(code, "未知错误"), "data": data}


class BizError(Exception):
    """业务异常：由全局处理器转成统一响应体。"""

    def __init__(self, code: int, message: str | None = None, http_status: int = 200):
        self.code = code
        self.message = message or MESSAGES.get(code, "未知错误")
        self.http_status = http_status
        super().__init__(self.message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz(_req: Request, exc: BizError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=fail(exc.code, exc.message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {
            401: E_UNAUTHORIZED,
            403: E_FORBIDDEN,
            404: E_NOT_FOUND,
            409: E_CONFLICT,
            429: E_LIMITED,
        }
        code = mapping.get(exc.status_code, E_BAD_REQUEST)
        return JSONResponse(status_code=exc.status_code, content=fail(code))

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content=fail(E_BAD_REQUEST, "提交内容不完整或格式不正确，请检查后重试"),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:  # noqa: BLE001
        return JSONResponse(status_code=500, content=fail(E_INTERNAL))
