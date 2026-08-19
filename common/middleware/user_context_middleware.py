"""
用户上下文中间件（对应 ragent UserContextInterceptor）

解析请求头 `X-User-Id` / `X-Username` 填充 UserContext（contextvars），请求结束 finally 清理，
防止上下文污染；P7 前无认证（P4 决策 D3），P7 接入认证后仅替换此中间件即可。

实现为纯 ASGI middleware（async/await，无 callback 风格）：
    - 不依赖 starlette/fastapi，可在 D0.5 独立单测；
    - D0.9 factory 装配时作为 ASGI 中间件直接挂入（与 BaseHTTPMiddleware 语义等价）。

跳过非 HTTP 请求（如 lifespan）；预检请求（OPTIONS）放行并填充，避免 CORS 阻断时丢失上下文。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.user.config.UserContextInterceptor
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

from common.context.user_context import LoginUser, UserContext

logger = logging.getLogger(__name__)

# 请求头 → LoginUser 字段映射（小写 key，ASGI headers 为 bytes 元组列表）
_USER_ID_HEADER = "x-user-id"
_USERNAME_HEADER = "x-username"


class UserContextMiddleware:
    """
    用户上下文中间件（对应 Java UserContextInterceptor）

    Args:
        app: 下游 ASGI 应用
    """

    def __init__(self, app: Callable):
        self._app = app

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        user_id, username = _extract_user_headers(scope)
        if user_id is not None or username is not None:
            UserContext.set(LoginUser(user_id=user_id, username=username))
        try:
            await self._app(scope, receive, send)
        finally:
            UserContext.clear()


def _extract_user_headers(scope: Dict[str, Any]) -> Tuple[str | None, str | None]:
    """从 ASGI scope headers 提取 user_id / username（对应 Java 请求头解析）"""
    user_id: str | None = None
    username: str | None = None
    headers: List[Tuple[bytes, bytes]] = scope.get("headers") or []
    for key, value in headers:
        name = key.decode("latin-1").lower()
        if name == _USER_ID_HEADER:
            user_id = value.decode("utf-8") or None
        elif name == _USERNAME_HEADER:
            username = value.decode("utf-8") or None
    return user_id, username
