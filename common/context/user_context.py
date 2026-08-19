"""
用户上下文（对应 ragent context.LoginUser + UserContext）

LoginUser：当前登录用户的上下文快照（user_id/username/role/avatar）。
UserContext：基于 contextvars.ContextVar 的用户上下文容器——在 async 端点内，
set/clear 覆盖整请求生命周期，asyncio.create_task 自动复制当前 context（等效 Java TTL）。
未设置时 get_user_id() 兜底返回 "anonymous"（P7 前无认证，P4 决策 D3）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.context.LoginUser
    - com.nageoffer.ai.ragent.framework.context.UserContext
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Optional

from common.exception.business import ClientException

# 缺省用户 ID（P7 前无认证，请求未带用户头时兜底；对齐 P4 决策 D3）
DEFAULT_ANONYMOUS_USER_ID = "anonymous"

# 默认头像 URL（对齐 Java UserContextInterceptor.DEFAULT_AVATAR_URL）
DEFAULT_AVATAR_URL = "https://avatars.githubusercontent.com/u/583231?v=4"


@dataclass(frozen=True)
class LoginUser:
    """当前登录用户的上下文快照（对应 Java LoginUser）"""

    user_id: Optional[str] = None
    username: Optional[str] = None
    role: Optional[str] = None
    avatar: Optional[str] = None


# contextvars 用户上下文（等效 Java TransmittableThreadLocal 的跨协程传递）
_CONTEXT: contextvars.ContextVar[Optional[LoginUser]] = contextvars.ContextVar(
    "ragent_user_context", default=None
)


class UserContext:
    """用户上下文容器（对应 Java UserContext，全静态）"""

    @staticmethod
    def set(user: LoginUser) -> None:
        """设置当前请求的用户上下文"""
        _CONTEXT.set(user)

    @staticmethod
    def get() -> Optional[LoginUser]:
        """获取当前请求的用户上下文；未设置返回 None"""
        return _CONTEXT.get()

    @staticmethod
    def require_user() -> LoginUser:
        """获取当前用户，不存在则抛客户端异常（对应 Java requireUser）"""
        user = _CONTEXT.get()
        if user is None:
            raise ClientException("未获取到当前登录用户")
        return user

    @staticmethod
    def get_user_id() -> str:
        """获取当前用户 ID；未设置或缺失兜底 anonymous（对应 Java getUserId，P4 决策 D3 兜底）"""
        user = _CONTEXT.get()
        if user is None or user.user_id is None:
            return DEFAULT_ANONYMOUS_USER_ID
        return user.user_id

    @staticmethod
    def get_username() -> Optional[str]:
        """获取当前用户名；未设置返回 None（对应 Java getUsername）"""
        user = _CONTEXT.get()
        return user.username if user is not None else None

    @staticmethod
    def get_role() -> Optional[str]:
        """获取当前角色；未设置返回 None（对应 Java getRole）"""
        user = _CONTEXT.get()
        return user.role if user is not None else None

    @staticmethod
    def get_avatar() -> Optional[str]:
        """获取当前头像；未设置返回 None（对应 Java getAvatar）"""
        user = _CONTEXT.get()
        return user.avatar if user is not None else None

    @staticmethod
    def clear() -> None:
        """清理当前请求的用户上下文（请求结束由中间件调用，防上下文污染）"""
        _CONTEXT.set(None)

    @staticmethod
    def has_user() -> bool:
        """是否已存在用户上下文（对应 Java hasUser）"""
        return _CONTEXT.get() is not None
