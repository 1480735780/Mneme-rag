# -*- coding: utf-8 -*-
"""P2 消费幂等单测：common/idempotent/consume.py（对应 Java @IdempotentConsume + IdempotentConsumeAspect）

对齐 Java Lua SET NX GET PX 语义（CacheManager get+set 模拟，对齐既有 D 决策）：
    - 无状态（None）→ 置 CONSUMING 后执行 → 置 CONSUMED；
    - 已有 CONSUMING("0") → 消费中重复 → ClientException（延迟重试）；
    - 已有 CONSUMED("1") → 已完成 → 跳过（fn 不执行，返回 None）；
    - 执行异常 → 删除 key（可重试）。
"""
import asyncio

import pytest

from common.exception.business import ClientException
from common.idempotent.consume import (
    IdempotentConsumeGuard,
    IdempotentConsumeStatus,
    get_guard,
    idempotent_consume,
    set_guard,
)
from storage.cache import MemoryCacheManager


@pytest.fixture(autouse=True)
def _reset_guard():
    set_guard(None)
    yield
    set_guard(None)


def _injected_guard(key_timeout: float = 60) -> IdempotentConsumeGuard:
    guard = IdempotentConsumeGuard(cache=MemoryCacheManager(), key_timeout=key_timeout)
    set_guard(guard)
    return guard


def _run(coro):
    return asyncio.run(coro)


class TestIdempotentConsumeStatus:
    def test_consuming_is_error(self):
        assert IdempotentConsumeStatus.is_error("0") is True
        assert IdempotentConsumeStatus.is_error("1") is False
        assert IdempotentConsumeStatus.is_error(None) is False


class TestConsumeAsync:
    def test_first_consume_executes_and_marks_consumed(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:1")
        async def handle():
            calls.append(1)
            return "ok"

        assert _run(handle()) == "ok"
        assert len(calls) == 1
        assert _run(guard._cache.get("msg:1")) == "1"  # CONSUMED

    def test_consuming_duplicate_raises(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:2")
        async def handle():
            calls.append(1)
            return "ok"

        _run(guard._cache.set("msg:2", IdempotentConsumeStatus.CONSUMING.value, ttl=60))
        with pytest.raises(ClientException) as exc:
            _run(handle())
        assert "幂等标识：msg:2" in str(exc.value)
        assert len(calls) == 0

    def test_consumed_skips(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:3")
        async def handle():
            calls.append(1)
            return "ok"

        _run(guard._cache.set("msg:3", IdempotentConsumeStatus.CONSUMED.value, ttl=60))
        assert _run(handle()) is None
        assert len(calls) == 0

    def test_exception_deletes_key_for_retry(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:4")
        async def handle():
            calls.append(1)
            raise ValueError("boom")

        with pytest.raises(ValueError):
            _run(handle())
        assert _run(guard._cache.get("msg:4")) is None  # 已删除可重试
        assert len(calls) == 1

    def test_key_prefix_composition(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key_prefix="order:", key="msg:5")
        async def handle():
            calls.append(1)
            return "ok"

        _run(handle())
        assert _run(guard._cache.get("order:msg:5")) == "1"
        assert _run(guard._cache.get("msg:5")) is None

    def test_key_fn_resolution(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key_fn=lambda args, kwargs: f"msg:{args[0]}")
        async def handle(msg_id):
            calls.append(1)
            return "ok"

        _run(handle("a"))
        assert _run(guard._cache.get("msg:a")) == "1"
        assert len(calls) == 1


class TestConsumeSync:
    def test_first_consume_executes(self):
        _injected_guard()
        calls = []

        @idempotent_consume(key="msg:s1")
        def handle():
            calls.append(1)
            return "ok"

        assert handle() == "ok"
        assert len(calls) == 1

    def test_consuming_duplicate_raises(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:s2")
        def handle():
            calls.append(1)
            return "ok"

        _run(guard._cache.set("msg:s2", IdempotentConsumeStatus.CONSUMING.value, ttl=60))
        with pytest.raises(ClientException):
            handle()
        assert len(calls) == 0
