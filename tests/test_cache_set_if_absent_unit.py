# -*- coding: utf-8 -*-
"""
R-C CacheManager.set_if_absent 单测（原子「不存在才写入」，对齐 Java Redisson setIfAbsent）

覆盖：
    - Memory：首占 True / 重复 False / 删除后可再占 / 首占值不被覆盖 / TTL 非法拒绝
    - 基类兜底语义（get-then-set 桩形态）：布尔语义一致
    - Redis 桩：SET NX EX 参数透传（nx=True / ex=int(ttl)）+ 已存在返回 False + TTL 非法 False
    - AgentRunGate 跨实例原子：两个 gate 共享同一 cache，同用户并发占位仅一方成功
    - IdempotentConsume 并发竞态：并发双消费恰好一方执行、另一方幂等拒绝（R-C 前存在双置位窗口）
"""
import asyncio

from agent.run_gate import AgentRunGate
from common.exception.business import ClientException
from common.idempotent.consume import IdempotentConsumeGuard
from storage.cache.client import CacheManager, MemoryCacheManager, RedisCacheManager


class _BareCache(CacheManager):
    """仅实现 get/set/delete：走基类 get-then-set 兜底语义"""

    def __init__(self):
        self._data = {}

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value, ttl=None):
        self._data[key] = value
        return True

    async def delete(self, key):
        return self._data.pop(key, None) is not None


class _FakeRedis:
    """记录 SET NX 参数的假 redis 客户端（服务端语义：nx 且键已存在 → None）"""

    def __init__(self):
        self.store = {}
        self.set_calls = []

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append({"key": key, "nx": nx, "ex": ex})
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        return self.store.pop(key, None) is not None


class TestMemoryAtomic:
    def test_first_true_second_false(self):
        async def run():
            cache = MemoryCacheManager()
            first = await cache.set_if_absent("k", "v1", ttl=60)
            second = await cache.set_if_absent("k", "v2", ttl=60)
            value = await cache.get("k")
            return first, second, value

        first, second, value = asyncio.run(run())
        assert first is True
        assert second is False
        assert value == "v1"  # 首占值不被覆盖

    def test_delete_then_reacquire(self):
        async def run():
            cache = MemoryCacheManager()
            assert await cache.set_if_absent("k", "v") is True
            await cache.delete("k")
            return await cache.set_if_absent("k", "v2")

        assert asyncio.run(run()) is True

    def test_ttl_invalid_rejected(self):
        async def run():
            cache = MemoryCacheManager()
            zero = await cache.set_if_absent("k", "v", ttl=0)
            negative = await cache.set_if_absent("k2", "v", ttl=-1)
            return zero, negative

        zero, negative = asyncio.run(run())
        assert zero is False
        assert negative is False


class TestBaseFallback:
    def test_bare_cache_boolean_semantics(self):
        async def run():
            cache = _BareCache()
            first = await cache.set_if_absent("k", "v")
            second = await cache.set_if_absent("k", "v2")
            return first, second, await cache.get("k")

        first, second, value = asyncio.run(run())
        assert first is True
        assert second is False
        assert value == "v"


class TestRedisSetNx:
    def test_nx_ex_passthrough(self):
        fake = _FakeRedis()
        manager = RedisCacheManager(redis=fake)

        async def run():
            first = await manager.set_if_absent("k", "v", ttl=60)
            second = await manager.set_if_absent("k", "v2", ttl=60)
            return first, second

        first, second = asyncio.run(run())
        assert first is True
        assert second is False  # 服务端 NX：键已存在 → None → False
        assert fake.set_calls[0]["nx"] is True and fake.set_calls[0]["ex"] == 60
        assert fake.set_calls[1]["nx"] is True

    def test_no_ttl_omits_ex(self):
        fake = _FakeRedis()
        manager = RedisCacheManager(redis=fake)

        async def run():
            return await manager.set_if_absent("k", "v")

        assert asyncio.run(run()) is True
        assert fake.set_calls[0]["ex"] is None

    def test_invalid_ttl_rejected_before_redis(self):
        fake = _FakeRedis()
        manager = RedisCacheManager(redis=fake)

        async def run():
            result = await manager.set_if_absent("k", "v", ttl=0)
            return result, len(fake.set_calls)

        result, calls = asyncio.run(run())
        assert result is False
        assert calls == 0


class TestAgentRunGateCrossInstance:
    def test_two_gates_shared_cache_exclusive(self):
        """多节点形态：两个 gate 实例（= 两个节点）共享 Redis，占位原子互斥"""
        cache = MemoryCacheManager()
        gate_a = AgentRunGate(cache, sse_timeout_ms=1000)
        gate_b = AgentRunGate(cache, sse_timeout_ms=1000)

        async def run():
            release_a = await gate_a.acquire("u1", "t1", "c1")
            try:
                await gate_b.acquire("u1", "t2", "c2")
                raised = False
            except ClientException as exc:
                raised = "当前会话处理中" in str(exc)
            await release_a()
            release_b = await gate_b.acquire("u1", "t2", "c2")  # A 释放后 B 可占
            await release_b()
            return raised

        assert asyncio.run(run()) is True

    def test_concurrent_acquire_single_winner(self):
        """并发占位恰好一方成功（Memory 实例锁原子）"""
        cache = MemoryCacheManager()
        gate = AgentRunGate(cache, sse_timeout_ms=1000)

        async def run():
            results = await asyncio.gather(
                gate.acquire("u1", "t1", "c1"),
                gate.acquire("u1", "t2", "c2"),
                return_exceptions=True,
            )
            ok = [r for r in results if not isinstance(r, Exception)]
            rejected = [r for r in results if isinstance(r, ClientException)]
            return len(ok), len(rejected)

        ok, rejected = asyncio.run(run())
        assert (ok, rejected) == (1, 1)


class TestIdempotentConsumeRace:
    def test_concurrent_consume_single_winner(self):
        """并发双消费恰好一方执行（set_if_absent 原子占位，R-C 前存在双置位竞态窗口）"""
        cache = MemoryCacheManager()
        guard = IdempotentConsumeGuard(cache=cache, key_timeout=60)
        executed = []

        async def body(tag):
            executed.append(tag)
            await asyncio.sleep(0.01)
            return tag

        async def run():
            results = await asyncio.gather(
                guard.consume("job-1", lambda: body("a"), async_fn=True),
                guard.consume("job-1", lambda: body("b"), async_fn=True),
                return_exceptions=True,
            )
            ok = [r for r in results if not isinstance(r, Exception)]
            rejected = [r for r in results if isinstance(r, ClientException)]
            return ok, rejected, executed

        ok, rejected, executed = asyncio.run(run())
        assert len(ok) == 1
        assert len(rejected) == 1
        assert executed == [ok[0]]

    def test_consumed_then_skip(self):
        cache = MemoryCacheManager()
        guard = IdempotentConsumeGuard(cache=cache, key_timeout=60)

        async def run():
            first = await guard.consume("job-1", lambda: "r1", async_fn=False)
            second = await guard.consume("job-1", lambda: "r2", async_fn=False)
            return first, second

        first, second = asyncio.run(run())
        assert first == "r1"
        assert second is None  # CONSUMED → 跳过
