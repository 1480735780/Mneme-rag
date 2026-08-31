# -*- coding: utf-8 -*-
"""
会话管理器单元测试：SessionManager（对应 Java Sa-Token StpUtil 能力等价）

覆盖：
    - login 生成 token + 会话可解析（user_id/username/role/avatar 往返）
    - resolve 未命中 / 已登出返回 None
    - logout 后 token 失效（服务端主动登出语义）
    - 会话 TTL 过期（可注入时钟）
    - 存储介质：内存兜底 / Redis CacheManager 均可用
"""
import asyncio
import time

import pytest

from storage.cache import CacheManager, MemoryCacheManager
from user.service.session_manager import SessionManager


class _FakeClock:
    def __init__(self, start=1000.0):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds


class _FakeRedis(CacheManager):
    """最小 Redis 假实现：直接透传 MemoryCacheManager 行为，验证介质无关"""

    def __init__(self):
        self._inner = MemoryCacheManager()

    async def get(self, key):
        return await self._inner.get(key)

    async def set(self, key, value, ttl=None):
        return await self._inner.set(key, value, ttl)

    async def delete(self, key):
        return await self._inner.delete(key)


def _manager(cache=None, clock=None, ttl=604800):
    return SessionManager(cache=cache, clock=clock or time.monotonic, ttl_seconds=ttl)


def _user(**kw):
    data = {"user_id": "u-1", "username": "alice", "role": "admin", "avatar": ""}
    data.update(kw)
    return data


class TestSessionManager:
    def test_login_resolve_roundtrip(self):
        async def run():
            m = _manager()
            token = await m.login(_user())
            assert token
            session = await m.resolve(token)
            assert session["user_id"] == "u-1"
            assert session["username"] == "alice"
            assert session["role"] == "admin"

        asyncio.run(run())

    def test_login_generates_unique_tokens(self):
        async def run():
            m = _manager()
            t1 = await m.login(_user())
            t2 = await m.login(_user())
            assert t1 != t2

        asyncio.run(run())

    def test_resolve_unknown_token_none(self):
        async def run():
            m = _manager()
            assert await m.resolve("no-such-token") is None

        asyncio.run(run())

    def test_logout_invalidates_token(self):
        async def run():
            m = _manager()
            token = await m.login(_user())
            assert await m.resolve(token) is not None
            assert await m.logout(token) is True
            assert await m.resolve(token) is None
            # 重复登出返回 False
            assert await m.logout(token) is False

        asyncio.run(run())

    def test_ttl_expiry(self):
        async def run():
            # MemoryCacheManager 用 time.monotonic 判定过期，注入短 TTL + 真实等待
            m = _manager(ttl=0.05)
            token = await m.login(_user())
            assert await m.resolve(token) is not None
            await asyncio.sleep(0.1)
            assert await m.resolve(token) is None  # 过期失效

        asyncio.run(run())

    def test_ttl_not_expired_within_window(self):
        async def run():
            m = _manager(ttl=60)
            token = await m.login(_user())
            await asyncio.sleep(0.01)
            assert await m.resolve(token) is not None

        asyncio.run(run())

    def test_memory_cache_backend(self):
        async def run():
            m = _manager(cache=MemoryCacheManager())
            token = await m.login(_user())
            assert (await m.resolve(token))["user_id"] == "u-1"

        asyncio.run(run())

    def test_redis_backend(self):
        async def run():
            m = _manager(cache=_FakeRedis())
            token = await m.login(_user())
            assert (await m.resolve(token))["user_id"] == "u-1"
            await m.logout(token)
            assert await m.resolve(token) is None

        asyncio.run(run())

    def test_token_scheme_prefix(self):
        async def run():
            m = _manager()
            token = await m.login(_user())
            assert token.startswith("ragent_")  # 便于识别与多端隔离

        asyncio.run(run())

    def test_user_id_is_str(self):
        async def run():
            m = _manager()
            token = await m.login(_user(user_id="u-999"))
            assert (await m.resolve(token))["user_id"] == "u-999"

        asyncio.run(run())
