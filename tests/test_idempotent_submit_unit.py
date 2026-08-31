# -*- coding: utf-8 -*-
"""
F1 幂等装饰器单测：common/idempotent/submit.py（对应 Java @IdempotentSubmit + 切面）

覆盖（语义对齐 Java RLock.tryLock：拦截发生在「第一个请求执行期间」的并发重复）：
    - async 函数：首次放行返回结果 / 并发重复（同 key 持锁中）→ ClientException（默认消息 + 自定义消息）/
      异常释放锁后可重试 / 不同 key 不互斥
    - sync 函数：首次放行 / 并发重复拦截 / 异常释放锁
    - key 分支：显式 key / 未提供 key 用「签名 + 参数 md5」（同参数互斥、不同参数放行）
    - 兜底：未注入 guard 时默认内存 guard 可用
"""
import asyncio
import threading

import pytest

from common.exception.business import ClientException
from common.idempotent.submit import get_guard, idempotent_submit, set_guard
from rag.service.idempotent import DEFAULT_SUBMIT_MESSAGE, IdempotentSubmitGuard
from storage.cache import MemoryCacheManager


@pytest.fixture(autouse=True)
def _reset_guard():
    """每个用例前后重置全局 guard（隔离注册状态）"""
    set_guard(None)
    yield
    set_guard(None)


def _injected_guard() -> IdempotentSubmitGuard:
    """注入共享内存 cache 的 guard（测试可控），并注册为全局默认"""
    guard = IdempotentSubmitGuard(cache=MemoryCacheManager(), ttl=60)
    set_guard(guard)
    return guard


def _run(coro):
    return asyncio.run(coro)


# ==================== async 路径 ====================


class TestAsyncIdempotent:
    def test_first_call_passes_and_returns(self):
        _injected_guard()

        calls = []

        @idempotent_submit(key="create:alice")
        async def create_user():
            calls.append(1)
            return "uid-1"

        assert _run(create_user()) == "uid-1"
        assert len(calls) == 1

    def test_concurrent_duplicate_blocked_default_message(self):
        _injected_guard()
        entered = asyncio.Event()
        release = asyncio.Event()
        blocked = []

        @idempotent_submit(key="create:alice")
        async def create_user():
            entered.set()
            await release.wait()  # 持锁挂起：模拟第一个请求执行中
            return "uid-1"

        async def run():
            first = asyncio.create_task(create_user())
            await entered.wait()
            with pytest.raises(ClientException) as exc_info:
                await create_user()  # 同 key 并发第二个 → 拦截
            blocked.append(exc_info.value.error_message)
            release.set()
            return await first

        assert _run(run()) == "uid-1"
        assert blocked == [DEFAULT_SUBMIT_MESSAGE]

    def test_concurrent_duplicate_custom_message(self):
        _injected_guard()
        entered = asyncio.Event()
        release = asyncio.Event()

        @idempotent_submit(key="create:alice", message="请勿重复提交")
        async def create_user():
            entered.set()
            await release.wait()
            return "uid-1"

        async def run():
            first = asyncio.create_task(create_user())
            await entered.wait()
            with pytest.raises(ClientException) as exc_info:
                await create_user()
            assert exc_info.value.error_message == "请勿重复提交"
            release.set()
            return await first

        assert _run(run()) == "uid-1"

    def test_exception_releases_lock(self):
        _injected_guard()
        fail = True

        @idempotent_submit(key="create:bob")
        async def create_user():
            if fail:
                raise RuntimeError("boom")
            return "uid-2"

        with pytest.raises(RuntimeError):
            _run(create_user())
        # 锁已释放 → 后续调用放行
        fail = False
        assert _run(create_user()) == "uid-2"

    def test_different_keys_not_blocked(self):
        _injected_guard()
        entered = asyncio.Event()
        release = asyncio.Event()
        second_ok = []

        @idempotent_submit(key="create:a")
        async def create_a():
            entered.set()
            await release.wait()
            return "a"

        @idempotent_submit(key="create:b")
        async def create_b():
            return "b"

        async def run():
            first = asyncio.create_task(create_a())
            await entered.wait()
            second_ok.append(await create_b())  # 不同 key → 不互斥
            release.set()
            await first
            return True

        assert _run(run()) is True
        assert second_ok == ["b"]

    def test_key_fn_extracts_stable_key(self):
        _injected_guard()
        entered = asyncio.Event()
        release = asyncio.Event()
        blocked_same_user = []

        class Service:
            """对齐真实 UserService.create（self 占 args[0]，params 在 args[1]）"""

            @idempotent_submit(key_fn=lambda args, kwargs: f"user:create:{args[1].get('username')}")
            async def create(self, params):
                if params["username"] == "alice":
                    entered.set()
                    await release.wait()  # 仅 alice 挂起持锁；bob 立即返回避免死锁
                return params["username"]

        svc = Service()

        async def run():
            first = asyncio.create_task(svc.create({"username": "alice"}))
            await entered.wait()
            # 同 username → 同 key_fn key → 并发拦截
            with pytest.raises(ClientException):
                await svc.create({"username": "alice"})
            blocked_same_user.append(True)
            # 不同 username → 不同 key_fn key → 放行
            other_ok = await svc.create({"username": "bob"})
            release.set()
            return await first, other_ok

        first, other_ok = _run(run())
        assert first == "alice"
        assert other_ok == "bob"
        assert blocked_same_user == [True]

    def test_default_key_binds_signature_and_args(self):
        _injected_guard()
        entered = asyncio.Event()
        release = asyncio.Event()
        blocked_same_arg = []
        other_ok = []

        @idempotent_submit()
        async def create_user(username):
            if username == "alice":
                entered.set()
                await release.wait()  # 仅 alice 挂起持锁；bob 立即返回避免死锁
            return username

        async def run():
            first = asyncio.create_task(create_user("alice"))
            await entered.wait()
            # 同函数同参数 → 同默认 key → 拦截
            with pytest.raises(ClientException):
                await create_user("alice")
            blocked_same_arg.append(True)
            # 不同参数 → 不同默认 key → 放行
            other_ok.append(await create_user("bob"))
            release.set()
            return await first

        assert _run(run()) == "alice"
        assert blocked_same_arg == [True]
        assert other_ok == ["bob"]


# ==================== sync 路径 ====================


class TestSyncIdempotent:
    def test_first_call_passes(self):
        _injected_guard()

        @idempotent_submit(key="op:1")
        def do_op():
            return "done"

        assert do_op() == "done"

    def test_concurrent_duplicate_blocked(self):
        _injected_guard()
        entered = threading.Event()
        release = threading.Event()
        blocked = []

        @idempotent_submit(key="op:1")
        def do_op():
            entered.set()
            release.wait(5)  # 持锁挂起：模拟第一个请求执行中
            return "ok"

        def worker():
            do_op()

        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(5)
        with pytest.raises(ClientException) as exc_info:
            do_op()  # 同 key 并发第二个（主线程）→ 拦截
        blocked.append(exc_info.value.error_message)
        release.set()
        thread.join(timeout=5)
        assert blocked == [DEFAULT_SUBMIT_MESSAGE]

    def test_exception_releases_lock(self):
        _injected_guard()
        fail = True

        @idempotent_submit(key="op:2")
        def do_op():
            if fail:
                raise RuntimeError("boom")
            return "ok"

        with pytest.raises(RuntimeError):
            do_op()
        fail = False
        assert do_op() == "ok"


# ==================== 兜底 ====================


class TestFallbackGuard:
    def test_default_memory_guard_when_not_injected(self):
        # 未 set_guard → 默认内存 guard 兜底，仍可拦截并发重复
        entered = asyncio.Event()
        release = asyncio.Event()
        blocked = []

        @idempotent_submit(key="fallback:1")
        async def fn():
            entered.set()
            await release.wait()
            return "x"

        async def run():
            first = asyncio.create_task(fn())
            await entered.wait()
            with pytest.raises(ClientException):
                await fn()
            blocked.append(True)
            release.set()
            return await first

        assert _run(run()) == "x"
        assert blocked == [True]

    def test_get_guard_returns_injected(self):
        guard = _injected_guard()
        assert get_guard() is guard
