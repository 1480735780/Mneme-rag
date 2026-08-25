"""
全局异常处理器（对应 ragent web.GlobalExceptionHandler）

将异常统一映射为 Result JSON 返回：
    - RequestValidationError（FastAPI 参数校验失败）→ CLIENT_ERROR + 首个字段错误消息；
    - AbstractException 族 → 按异常自身 error_code / error_message 映射（cause 存在时记完整堆栈）；
    - 其余异常（Throwable 兜底）→ SERVICE_ERROR 默认 + 记堆栈，避免向客户端泄露内部细节。

P4 范围裁剪（对齐计划 D0.8）：
    - 不移植 SaToken 未登录/无角色分支（P7 认证）；
    - 不移植文件上传大小分支（P5 知识库/对象存储域）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.web.GlobalExceptionHandler
"""
from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common.exception.business import AbstractException
from common.exception.errorcode import BaseErrorCode
from common.response.result import Results
from common.web.serializer import result_to_dict

logger = logging.getLogger(__name__)


def register_exception_handlers(app: Any) -> None:
    """
    注册全局异常处理器到 FastAPI 应用

    Args:
        app: FastAPI 实例
    """
    assert isinstance(app, FastAPI)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 对齐 Java validExceptionHandler：取首个字段错误消息
        first = next(iter(exc.errors()), None)
        message = first.get("msg", "") if first else ""
        result = Results.failure(BaseErrorCode.CLIENT_ERROR.code, message or "请求参数错误")
        return JSONResponse(status_code=200, content=result_to_dict(result))

    @app.exception_handler(AbstractException)
    async def _abstract_exception_handler(request: Request, exc: AbstractException) -> JSONResponse:
        if exc.cause is not None:
            logger.error(
                "[%s] %s [ex] %s",
                request.method, _request_url(request), exc, exc_info=exc.cause,
            )
        else:
            logger.error(
                "[%s] %s [ex] %s\n%s",
                request.method, _request_url(request), exc, _stack_preview(exc),
            )
        result = Results.failure(exc.error_code, exc.error_message)
        # 未认证（A000401）映射 HTTP 401，供前端认证过期跳登录；其余业务错误保持 HTTP 200 + 业务码
        http_status = 401 if exc.error_code == BaseErrorCode.UNAUTHORIZED.code else 200
        return JSONResponse(status_code=http_status, content=result_to_dict(result))

    @app.exception_handler(Exception)
    async def _default_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # 兜底：不向客户端暴露内部异常细节（对齐 Java defaultErrorHandler → Results.failure()）
        logger.error("[%s] %s ", request.method, _request_url(request), exc_info=exc)
        result = Results.failure()
        return JSONResponse(status_code=200, content=result_to_dict(result))


def _request_url(request: Any) -> str:
    url = str(request.url)
    return url


def _stack_preview(exc: BaseException) -> str:
    """前 5 行堆栈预览（对齐 Java stackTraceBuilder 前 5 帧）"""
    stack = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return "".join(stack)
