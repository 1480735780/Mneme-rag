# -*- coding: utf-8 -*-
"""
common.middleware.request_log_middleware - 请求日志中间件（ASGI）

Java 侧无对应过滤器（调研结论：UserContextInterceptor / UploadRateLimitFilter /
DemoModeInterceptor 均不记请求日志，访问日志靠中间件容器缺省）。Python 侧补薄实现：

    - 记录一条结构化日志（每 HTTP 请求一行）：method / path / 脱敏 query / status /
      耗时 ms / 调用方用户（X-User-Id 头，复用 user_context_middleware 的头解析）。
    - query 脱敏：参数值统一打码为 <redacted>（对应 LogSafe 的敏感不落原则——
      query 可能携带 token 等敏感参数，只留 key 面）。
    - 放中间件链**最外层**（先 add_middleware 即最内层；后 add 为外层）：
      不依赖 UserContext contextvar，用户直接读请求头，从而能包住 CORS 之外的整条链。
    - 非 HTTP 请求（lifespan 等）与 WebSocket 之外的 scope 直接透传不打日志。
    - 异常路径：send 前抛异常 → status=0 记录（错误详情由全局异常处理器输出）。

对应 ragent 参考：
    - 无（Java 访问日志未实现，此为本项目自研项，2026-09-16 登记）
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from common.middleware.user_context_middleware import _extract_user_headers

logger = logging.getLogger(__name__)

# 敏感头：不随日志输出（即使只打 key，值也绝不落）
_SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization"}


class RequestLogMiddleware:
    """
    请求访问日志中间件（每 HTTP 请求一条 INFO；慢请求可按需调级）

    Args:
        app: 下游 ASGI 应用
    """

    def __init__(self, app: Callable):
        self._app = app

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        started = time.perf_counter()
        status: int = 0

        async def _send_wrapper(message: Dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message.get("status", 0)
            await send(message)

        try:
            # receive 透传原样；send 换成 wrapper 以捕获响应状态码
            await self._app(scope, receive, _send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._record(scope, status, elapsed_ms)

    # ------------------------------------------------------------------ #

    def _record(self, scope: Dict[str, Any], status: int, elapsed_ms: float) -> None:
        user_id, _username = _extract_user_headers(scope)
        logger.info(
            "request method=%s path=%s query=%s status=%s cost_ms=%.1f user=%s",
            (scope.get("method") or "?").upper(),
            scope.get("path") or "/",
            _redact_query(scope.get("query_string") or b""),
            status if status else "-",
            elapsed_ms,
            user_id or "-",
        )


def _redact_query(query_string: bytes) -> str:
    """query_string 脱敏：保留参数键与结构，值统一打码（对齐 LogSafe 敏感不落原则）"""
    if not query_string:
        return "-"
    try:
        raw = query_string.decode("utf-8", errors="replace")
    except Exception:
        return "<unparseable>"
    pairs = []
    for segment in raw.split("&"):
        if not segment:
            continue
        key, sep, _value = segment.partition("=")
        pairs.append(f"{key}={sep and '<redacted>' or ''}")
    return "&".join(pairs) or "-"