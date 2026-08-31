# -*- coding: utf-8 -*-
"""P2 RedisKeySerializer 单测：storage/cache/key_serializer.py + RedisCacheManager key_prefix"""
import pytest

from storage.cache import RedisCacheManager
from storage.cache.key_serializer import RedisKeySerializer


class TestRedisKeySerializer:
    def test_serialize_with_prefix(self):
        ser = RedisKeySerializer("rag:")
        assert ser.serialize("kb:1") == b"rag:kb:1"

    def test_serialize_no_prefix(self):
        ser = RedisKeySerializer()
        assert ser.serialize("kb:1") == b"kb:1"

    def test_deserialize_utf8(self):
        ser = RedisKeySerializer("rag:")
        assert ser.deserialize(b"rag:kb:1") == "rag:kb:1"

    def test_key_prefix_property(self):
        assert RedisKeySerializer("x:").key_prefix == "x:"
        assert RedisKeySerializer().key_prefix == ""


class _FakeRedis:
    """记录写入键/值的最简 Redis 桩（仅覆盖 get/set/delete）"""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def delete(self, key):
        return self.store.pop(key, None) is not None


class TestRedisCacheManagerPrefix:
    def test_prefix_applied_on_all_ops(self):
        import asyncio
        redis = _FakeRedis()
        mgr = RedisCacheManager(redis=redis, key_prefix="app:")
        async def scenario():
            await mgr.set("k", {"a": 1}, ttl=60)
            assert redis.store == {"app:k": '{"a": 1}'}  # 物理键带前缀
            assert await mgr.get("k") == {"a": 1}
            assert await mgr.delete("k") is True
            assert redis.store == {}
        asyncio.run(scenario())

    def test_no_prefix_behavior_unchanged(self):
        import asyncio
        redis = _FakeRedis()
        mgr = RedisCacheManager(redis=redis)
        async def scenario():
            await mgr.set("k", 1)
            assert redis.store == {"k": "1"}
            assert await mgr.get("k") == 1
        asyncio.run(scenario())
