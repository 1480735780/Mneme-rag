# -*- coding: utf-8 -*-
"""P10.1 T1.1 单测：ObservationQueue（D2）与 CallScope（D5）

覆盖：put_nowait 非阻塞与满载丢弃计数、双阈值先到先触发（条数/时间窗各自触发且
不提前flush）、stop_and_drain 不丢已入队（含停止位竞态与停机后投递）、
flush 失败吞掉计数（观测不反噬业务）、stats 快照；scope overlay 合并/退出还原/
unknown 兜底、消费端 task 读不到发射 task 的 scope（发射点物化的依据演示）。
异步统一 asyncio.run() 包裹（房式约定）。
"""
import asyncio

from core.llm.observation.collector import ObservationStats
from core.llm.observation.queue import ObservationQueue
from core.llm.observation.scope import CallScope, current_scope, llm_call_scope


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """轮询等待条件成立（测试辅助，不做 busy-loop）"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# ===================== ObservationQueue（D2） =====================


def test_put_nowait_nonblocking_and_drop_when_full():
    flushed: list = []

    async def handler(batch):
        flushed.extend(batch)

    async def scenario():
        q = ObservationQueue(handler, maxsize=2, flush_count=100, flush_interval=60.0)
        q.start()
        assert q.put_nowait("a") is True
        assert q.put_nowait("b") is True
        assert q.put_nowait("c") is False  # 满即丢，不抛异常、不 await（D2 硬约束）
        assert q.put_nowait("d") is False
        assert q.stats.enqueued == 2 and q.stats.dropped_full == 2
        await q.stop_and_drain()
        assert sorted(flushed) == ["a", "b"]
        assert q.stats.flushed == 2

    asyncio.run(scenario())


def test_count_threshold_triggers_before_interval():
    """条数阈值先到先触发：间隔窗口未到也立即攒批 flush"""
    flushed: list = []

    async def handler(batch):
        flushed.append(list(batch))

    async def scenario():
        q = ObservationQueue(handler, flush_count=3, flush_interval=60.0)
        q.start()
        q.put_nowait(1)
        q.put_nowait(2)
        await asyncio.sleep(0.15)
        assert flushed == []  # 窗口未到、条数未满：不 flush
        q.put_nowait(3)
        assert await _wait_until(lambda: len(flushed) == 1)
        assert flushed[0] == [1, 2, 3]  # 单批攒齐
        await q.stop_and_drain()
        assert len(flushed) == 1

    asyncio.run(scenario())


def test_time_threshold_triggers_before_count():
    """时间窗阈值先到先触发：条数未满也按窗 flush（自首个待发事件起算）"""
    flushed: list = []

    async def handler(batch):
        flushed.append(list(batch))

    async def scenario():
        q = ObservationQueue(handler, flush_count=100, flush_interval=0.25)
        q.start()
        q.put_nowait("x")
        q.put_nowait("y")
        assert await _wait_until(lambda: len(flushed) == 1)
        assert flushed[0] == ["x", "y"]
        await q.stop_and_drain()
        assert len(flushed) == 1

    asyncio.run(scenario())


def test_stop_and_drain_no_loss():
    """drain 契约：生产者停后，已入队数据一条不丢（kill -9 之外，D2/Q1）"""
    flushed: list = []

    async def handler(batch):
        flushed.extend(batch)

    async def scenario():
        q = ObservationQueue(handler, flush_count=1000, flush_interval=60.0)
        q.start()

        async def producer(n):
            for i in range(n):
                q.put_nowait(i)
                await asyncio.sleep(0.001)

        await producer(50)
        q.put_nowait(50)
        q.put_nowait(51)
        q.put_nowait(52)
        await q.stop_and_drain()
        assert sorted(flushed) == list(range(53))
        assert q.stats.enqueued == 53 and q.stats.flushed == 53 and q.stats.dropped_full == 0

    asyncio.run(scenario())


def test_put_after_stop_counts_as_dropped():
    flushed: list = []

    async def handler(batch):
        flushed.extend(batch)

    async def scenario():
        q = ObservationQueue(handler, flush_count=10, flush_interval=60.0)
        q.start()
        q.put_nowait("in")
        await q.stop_and_drain()
        assert q.put_nowait("late") is False  # 停机后投递计入丢弃，不静默入队等死
        assert q.stats.dropped_full == 1 and "late" not in flushed

    asyncio.run(scenario())


def test_flush_error_swallowed_and_counted():
    """handler 抛异常：吞掉 + 计数（观测故障不得反噬业务，执行纪律 4）"""
    flushed: list = []

    async def bad_handler(batch):
        raise RuntimeError("db down")

    async def scenario():
        q = ObservationQueue(bad_handler, flush_count=1, flush_interval=60.0)
        q.start()
        q.put_nowait(1)
        q.put_nowait(2)
        await q.stop_and_drain()  # 不向外抛
        assert q.stats.flush_errors == 2 and q.stats.flushed == 0

    asyncio.run(scenario())


def test_stats_snapshot_shape():
    stats = ObservationStats()
    assert stats.snapshot() == {
        "enqueued": 0, "dropped_full": 0, "flushed": 0, "flush_errors": 0,
        "breaker_events": 0,
    }


def test_start_is_idempotent():
    flushed: list = []

    async def handler(batch):
        flushed.extend(batch)

    async def scenario():
        q = ObservationQueue(handler, flush_count=1, flush_interval=60.0)
        q.start()
        q.start()  # 幂等：不得起两个消费循环造成重复 flush
        q.put_nowait(1)
        await q.stop_and_drain()
        assert flushed == [1]

    asyncio.run(scenario())


# ===================== CallScope（D5） =====================


def test_scope_defaults_unknown_and_never_none():
    assert current_scope() == CallScope()  # 未设置即 unknown 兜底（非 None）
    assert current_scope().scene == "unknown" and current_scope().purpose == "unknown"


def test_scope_overlay_merge_and_reset():
    with llm_call_scope(scene="chat", purpose="rewrite") as outer:
        assert outer.scene == "chat" and outer.trace_id is None
        with llm_call_scope(trace_id="t1", user_id="u1") as inner:
            # overlay：未给定的继承外层（调用点只标 scene/purpose，trace 由外层带入）
            assert inner.scene == "chat" and inner.purpose == "rewrite"
            assert inner.trace_id == "t1" and inner.user_id == "u1"
        assert current_scope().trace_id is None  # 退出即还原
        assert current_scope().scene == "chat"
    assert current_scope() == CallScope()  # 全部退出回落 unknown


def test_scope_consumer_task_reads_default_air():
    """发射点物化的依据演示：flush 消费 task 在 scope 外创建，读 contextvar 只会读到
    unknown——事件必须在发射点（scope 内）物化进 payload（D5 硬纪律）。"""
    seen: list = []

    async def consumer():
        await asyncio.sleep(0.01)
        seen.append(current_scope().scene)

    async def scenario():
        task = asyncio.create_task(consumer())  # 消费 task 在 scope 之外创建
        with llm_call_scope(scene="chat"):
            pass  # 此处若依赖「flush 时再读 scope」只会读到 unknown
        await task
        assert seen == ["unknown"]

    asyncio.run(scenario())
