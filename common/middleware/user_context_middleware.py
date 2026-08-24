"""
用户上下文中间件（对应 ragent UserContextInterceptor，P7 认证双模式）

双模式（决策 D2）：
    - auth_enabled=False（默认）：解析请求头 X-User-Id / X-Username 填充 UserContext（P4 现状，匿名兜底）
    - auth_enabled=True：解析 Authorization: Bearer <token> → 会话解析 → UserContext（含 role/avatar），
      覆盖 X-User-Id 直填语义（D2）；无 token / 非法 token → 不填充（匿名兜底）
请求结束 finally 清理，防止上下文污染。

实现为纯 ASGI middleware（async/await，无 callback 风格）：
    - 不依赖 starlette/fastapi，可在独立单测；
    - factory 装配时注入 auth_enabled / session_manager（经配置与容器，见 factory）。

跳过非 HTTP 请求（如 lifespan）；预检请求（OPTIONS）放行，避免 CORS 阻断时丢失上下文。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.user.config.UserContextInterceptor
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from common.context.user_context import LoginUser, UserContext

logger = logging.getLogger(__name__)

# 请求头 → LoginUser 字段映射（小写 key，ASGI headers 为 bytes 元组列表）
_USER_ID_HEADER = "x-user-id"
_USERNAME_HEADER = "x-username"
_AUTH_HEADER = "authorization"
_BEARER_PREFIX = "bearer"


class UserContextMiddleware:
    """
    用户上下文中间件（对应 Java UserContextInterceptor，P7 认证双模式）

    Args:
        app:            下游 ASGI 应用
        auth_enabled:   是否启用 token 认证（P7 D2）；False 走 X-User-Id 直填
        session_manager: 会话解析器（auth_enabled=True 时可用；提供 async resolve(token) → 会话 dict）。
                        None 时运行时从 scope["app"].state.container.session_manager 延迟取
                        （容器由 lifespan 装配，见 factory）。
    """

    def __init__(self, app: Callable, auth_enabled: bool = False, session_manager: Optional[Any] = None):
        self._app = app
        self._auth_enabled = auth_enabled
        self._sessions = session_manager

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        user = await self._resolve_user(scope)
        if user is not None:
            UserContext.set(user)
        try:
            await self._app(scope, receive, send)
        finally:
            UserContext.clear()

    # ------------------------------------------------------------------ #

    async def _resolve_user(self, scope: Dict[str, Any]) -> Optional[LoginUser]:
        """按模式解析用户上下文；未命中返回 None"""
        if self._auth_enabled:
            return await self._resolve_by_token(scope)
        return _resolve_by_headers(scope)

    async def _resolve_by_token(self, scope: Dict[str, Any]) -> Optional[LoginUser]:
        """认证开启：Bearer token → 会话 → LoginUser（覆盖 X-User-Id 直填，D2）"""
        token = _extract_bearer_token(scope)
        if not token:
            return None
        sessions = self._sessions or self._container_sessions(scope)
        if sessions is None:
            return None
        session = await sessions.resolve(token)
        if not session:
            return None
        return LoginUser(
            user_id=session.get("user_id"),
            username=session.get("username"),
            role=session.get("role"),
            avatar=session.get("avatar"),
        )

    @staticmethod
    def _container_sessions(scope: Dict[str, Any]):
        """运行时从 app.state.container 延迟取会话管理器（容器由 lifespan 装配）"""
        try:
            container = scope.get("app").state.container
        except AttributeError:
            return None
        return getattr(container, "session_manager", None) if container is not None else None


def _extract_bearer_token(scope: Dict[str, Any]) -> Optional[str]:
    """从 Authorization: Bearer <token> 提取 token；非 Bearer / 缺失返回 None"""
    headers: List[Tuple[bytes, bytes]] = scope.get("headers") or []
    for key, value in headers:
        if key.decode("latin-1").lower() == _AUTH_HEADER:
            parts = value.decode("utf-8").split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == _BEARER_PREFIX:
                token = parts[1].strip()
                return token or None
            return None
    return None


def _resolve_by_headers(scope: Dict[str, Any]) -> Optional[LoginUser]:
    """关闭模式：X-User-Id / X-Username → LoginUser（现状不变）"""
    user_id, username = _extract_user_headers(scope)
    if user_id is None and username is None:
        return None
    return LoginUser(user_id=user_id, username=username)


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
