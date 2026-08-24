# -*- coding: utf-8 -*-
"""
user.service.auth_service - 认证服务（对应 Java AuthService + AuthServiceImpl）

组合 UserDao + SessionManager + password 实现登录/登出：
    - login：校验非空 → 查用户（软删过滤）→ 密码校验（哈希/明文兼容 D3）→ 建会话
    - logout：登出（token 由调用方从 Authorization 头解析后传入）

返回 snake_case dict（controller 层转 LoginVO camelCase）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.user.service.AuthService / AuthServiceImpl
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from common.exception.business import ClientException
from common.context.user_context import DEFAULT_AVATAR_URL
from user.dao.user_dao import UserDao
from user.service.password import verify_password
from user.service.session_manager import SessionManager


class AuthService:
    """认证服务：登录 / 登出（对应 Java AuthServiceImpl）"""

    def __init__(self, user_dao: UserDao, session_manager: SessionManager):
        self._users = user_dao
        self._sessions = session_manager

    async def login(self, username: Optional[str], password: Optional[str]) -> Dict[str, Any]:
        """登录：凭据校验 → 建会话 → 返回 {user_id, role, token, avatar}"""
        username = (username or "").strip()
        password = password or ""
        if not username or not password:
            raise ClientException("用户名或密码不能为空")
        user = self._users.find_by_username(username)
        if user is None or not verify_password(password, user.get("password")):
            raise ClientException("用户名或密码错误")
        token = await self._sessions.login(
            {
                "user_id": user.get("id"),
                "username": user.get("username"),
                "role": user.get("role"),
                "avatar": user.get("avatar"),
            }
        )
        avatar = user.get("avatar") or DEFAULT_AVATAR_URL
        return {
            "user_id": user.get("id"),
            "role": user.get("role"),
            "token": token,
            "avatar": avatar,
        }

    async def logout(self, token: Optional[str]) -> None:
        """登出：删除服务端会话（幂等，token 不存在也不报错）"""
        await self._sessions.logout(token)
