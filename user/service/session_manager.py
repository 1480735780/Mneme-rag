# -*- coding: utf-8 -*-
"""
user.service.session_manager - 会话管理器（对应 Java Sa-Token StpUtil 能力等价）

opaque token + 服务端会话：login(user) → token；resolve(token) → 会话；logout(token) → 失效。
语义与 Sa-Token 的「服务端会话、主动登出、可续期」一致（D1 决策）：
    - token 为随机 opaque（uuid4），存于 CacheManager（Redis TTL 7 天，缺省内存兜底）
    - 登出即删会话 → 之后 resolve 返回 None（服务端主动失效，非 JWT 的客户端自证）
    - 会话载荷存 user_id/username/role/avatar（对齐 LoginUser / CurrentUserVO）

存储介质经 CacheManager 抽象（async），InMemory / Redis 均无感知；时钟可注入（测试）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.user.config.SaTokenConfig / StpInterfaceImpl（能力等价）
    - com.nageoffer.ai.ragent.user.service.AuthService（login/logout 语义）
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, Optional

from storage.cache import CacheManager, MemoryCacheManager

# token 前缀（便于日志识别与多端隔离）
TOKEN_PREFIX = "ragent_"
# 会话缓存键前缀
_SESSION_KEY_PREFIX = "auth:session:"

# 默认会话有效期（秒）：7 天（对齐 Sa-Token 默认 timeout 语义）
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


class SessionManager:
    """会话管理器：opaque token 服务端会话（对应 Sa-Token 会话能力）"""

    def __init__(
        self,
        cache: Optional[CacheManager] = None,
        clock: Optional[Callable[[], float]] = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ):
        """
        Args:
            cache:      会话存储（CacheManager 抽象；None 时用 MemoryCacheManager 兜底，对齐 D1 缺省内存）
            clock:      可注入单调时钟（测试用）；默认 time.monotonic
            ttl_seconds:会话有效期秒数
        """
        self._cache = cache or MemoryCacheManager()
        self._clock = clock or time.monotonic
        self._ttl = ttl_seconds

    # ------------------------------------------------------------------ #

    async def login(self, user: Dict[str, Any]) -> str:
        """建立会话并返回 token（对应 StpUtil.login 语义）

        Args:
            user: 会话载荷（user_id 必填，其余 username/role/avatar 可选）

        Returns:
            str: opaque token
        """
        token = self._new_token()
        payload = {
            "user_id": user.get("user_id"),
            "username": user.get("username"),
            "role": user.get("role"),
            "avatar": user.get("avatar"),
        }
        await self._cache.set(self._key(token), payload, ttl=self._ttl)
        return token

    async def resolve(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """解析 token → 会话载荷；未命中 / 已登出 / 已过期 → None（对应 StpUtil.getLoginId 语义）"""
        if not token:
            return None
        return await self._cache.get(self._key(token))

    async def logout(self, token: Optional[str]) -> bool:
        """登出：删除会话，之后 resolve 返回 None；重复登出返回 False"""
        if not token:
            return False
        return bool(await self._cache.delete(self._key(token)))

    # ------------------------------------------------------------------ #

    @staticmethod
    def _new_token() -> str:
        return TOKEN_PREFIX + uuid.uuid4().hex

    @staticmethod
    def _key(token: str) -> str:
        return _SESSION_KEY_PREFIX + token
