"""
common.middleware - ASGI 中间件

    - user_context_middleware：用户上下文中间件（X-User-Id / X-Username 头 → UserContext）
    - request_log_middleware：请求访问日志（method/path/脱敏 query/status/耗时/用户）
"""
from common.middleware.request_log_middleware import RequestLogMiddleware
from common.middleware.user_context_middleware import (
    UserContextMiddleware,
    _extract_user_headers,
)

__all__ = [
    "UserContextMiddleware",
    "RequestLogMiddleware",
    "_extract_user_headers",
]
