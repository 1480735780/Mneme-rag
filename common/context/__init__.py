"""
common.context - 用户上下文

    - user_context：LoginUser + UserContext（contextvars 版用户上下文容器）
"""
from common.context.user_context import (
    DEFAULT_ANONYMOUS_USER_ID,
    DEFAULT_AVATAR_URL,
    LoginUser,
    UserContext,
)

__all__ = [
    "DEFAULT_ANONYMOUS_USER_ID",
    "DEFAULT_AVATAR_URL",
    "LoginUser",
    "UserContext",
]
