"""
common.middleware - ASGI 中间件

    - user_context_middleware：用户上下文中间件（X-User-Id / X-Username 头 → UserContext）
"""
from common.middleware.user_context_middleware import (
    UserContextMiddleware,
    _extract_user_headers,
)

__all__ = [
    "UserContextMiddleware",
    "_extract_user_headers",
]
