# -*- coding: utf-8 -*-
"""
user.security - 角色权限控制（对应 Java Sa-Token StpUtil.checkRole 能力等价）

@require_role(role)：FastAPI 依赖注入守卫——从 UserContext 取当前用户角色，
不满足抛 ClientException（对齐 StpUtil.checkRole 未授权语义，由全局异常处理器转 Result）。
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from common.context.user_context import UserContext
from common.exception.business import ClientException, UnauthorizedException


def require_role(role: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """要求当前用户具备指定角色，否则抛异常（未登录 → 401 UnauthorizedException；角色不符 → 无权限 ClientException）"""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_role = UserContext.get_role()
            if current_role is None:
                raise UnauthorizedException("未登录或登录已过期")
            if current_role != role:
                raise ClientException("无权限执行该操作")
            return await func(*args, **kwargs)
        return wrapper
    return decorator
