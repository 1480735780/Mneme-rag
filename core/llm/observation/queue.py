# -*- coding: utf-8 -*-
"""
core.llm.observation.queue - 有界异步观测队列（P10.1，决策 D2）

观测绝不耦合推理路径：写入端 put_nowait 满即丢（禁 await，流式首包延迟是刚性指标），
消费由后台批量 flush。flush 双阈值先到先触发：条数（默认 200）或时间窗（默认 1s，
自首个待发事件起算）——正常路径攒批，绝不因单条事件同步落库。

停机顺序契约（D2/Q1）：先停生产者 → stop_and_drain()（drain 一次队列，kill -9 之外
不丢已入队数据）→ 再释放 DB 等下游资源（app/factory.py lifespan 于 T1.2 接线）。
停止位之后禁止再投递（put_nowait 会计入 dropped_full 而非静默入队等死）。

任何 flush 失败吞掉并计数（flush_errors）——观测代码的故障不得反噬业务（执行纪律 4）；
丢弃/失败全部走 ObservationStats 计数器，禁止静默消失（「可观测的丢」）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, List, Optional

from core.llm.observation.collector import ObservationStats

# 批量落库回调（async (batch) -> None）；抛异常整批计入 flush_errors，不重试
FlushHandler = Callable[[List[Any]], Awaitable[None]]


class ObservationQueue:
    """
    有界异步观测队列（单队列承载 CallEvent / BreakerEvent，D12）

    Args:
        handler:        批量落库回调；抛异常整批计数 flush_errors（吞掉，不反噬业务）
        maxsize:        有界队列容量（满即丢，D2 硬约束）
        flush_count:    条数阈值（双阈值之一，先到先触发）
        flush_interval: 时间窗阈值秒数（自首个待发事件起算，双阈值之一）
        poll_interval:  空闲轮询秒数（stop 响应延迟上界，实现细节参数）
        stats:          外部注入计数器（默认自建；/metrics 读同一实例）
    """

    def __init__(
        self,
        handler: FlushHandler,
        *,
        maxsize: int = 1000,
        flush_count: int = 200,
        flush_interval: float = 1.0,
        poll_interval: float = 0.05,
        stats: Optional[ObservationStats] = None,
    ):
        if maxsize <= 0:
            raise ValueError("maxsize 必须为正（有界是 D2 硬约束，禁止无界积压）")
        self._handler = handler
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._flush_count = max(1, flush_count)
        self._flush_interval = flush_interval
        self._poll_interval = poll_interval
        self.stats = stats if stats is not None else ObservationStats()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    # ===================== 生产端（推理路径上，必须非阻塞） =====================

    def put_nowait(self, event: Any) -> bool:
        """非阻塞投递；满即丢并计数，返回是否成功入队（调用方不感知、不重试）"""
        if self._stopping:
            self.stats.dropped_full += 1
            return False
        try:
            self._queue.put_nowait(event)
            self.stats.enqueued += 1
            return True
        except asyncio.QueueFull:
            self.stats.dropped_full += 1
            return False

    # ===================== 消费端（后台任务） =====================

    def start(self) -> None:
        """启动后台 flush 循环（幂等；须在事件循环内调用）"""
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    def depth(self) -> int:
        """当前队列积压（/metrics 的 queue_depth 出口）"""
        return self._queue.qsize()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while not (self._stopping and self._queue.empty()):
            batch: List[Any] = []
            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                continue  # 队列空转：回循环头复查 stopping
            batch.append(item)
            if not self._stopping:
                # 双阈值窗口：条数到限或时间窗到期，先到先触发；
                # stop 置位时跳过窗口直接 flush（停机路径不等待）
                deadline = loop.time() + self._flush_interval
                while len(batch) < self._flush_count and not self._stopping:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        item = await asyncio.wait_for(
                            self._queue.get(),
                            timeout=min(remaining, self._poll_interval),
                        )
                    except asyncio.TimeoutError:
                        continue  # 时间片到期≠窗口到期，回内循环复查 deadline/stopping
                    batch.append(item)
            await self._flush(batch)

    async def _flush(self, batch: List[Any]) -> None:
        if not batch:
            return
        try:
            await self._handler(batch)
            self.stats.flushed += len(batch)
        except Exception:  # noqa: BLE001 — 观测故障不得反噬业务（D2），丢失计入 flush_errors
            self.stats.flush_errors += 1

    async def stop_and_drain(self) -> None:
        """
        停机 drain（D2/Q1）：置停止位 → 等后台循环退出 → 收尽剩余队列单次 flush。

        调用契约：先停生产者再调用本方法；返回后禁止再 put_nowait。
        """
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None:
            try:
                await task  # 协作式退出：循环至多再等一个 poll_interval
            except asyncio.CancelledError:
                pass
        # 兜底收尽（正常路径循环退出时队列应已空；此处保证契约字面成立）
        batch: List[Any] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        await self._flush(batch)
