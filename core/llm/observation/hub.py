# -*- coding: utf-8 -*-
"""
core.llm.observation.hub - 观测管线装配单元（P10.1 T1.2）

wiring 构建容器单例、factory lifespan 启停、发射点（T1.3 起）经 emit() 投递。

flush 落库经 asyncio.to_thread 出事件循环执行——SqlExecutor 每操作独立
engine.connect()（连接池线程安全）、InMemoryDatabaseClient 全路径 RLock，
200 行/批的同步 insert 不占 loop（D2：观测不反噬推理路径，流式首包是刚性指标；
顺序 flush 保证同一时刻至多一个落库批次在途）。

事件分派严格白名单：未知类型整批拒收（flush_errors 计数，观测管道不吞未知数据）；
T1.7 接入 BreakerEvent 时在此登记分支。CallEvent 落库前过成本固化点（T1.8：
unpriced 占位、pricing_snapshot 始终写，P10.3 注入真实计算器；异常吞掉计数）。
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Dict, Deque, List, Optional

from core.llm.observation.collector import ObservationStats
from core.llm.observation.dao import LlmCallDao
from core.llm.observation.events import BreakerEvent, CallEvent
from core.llm.observation.pricing import Pricer, apply_unpriced
from core.llm.observation.queue import ObservationQueue


class ObservationHub:
    """观测管线单例：emit() 投递 → 有界队列 → 批量 flush（off-loop）→ DAO 落库"""

    def __init__(
        self,
        dao: LlmCallDao,
        *,
        maxsize: int = 1000,
        flush_count: int = 200,
        flush_interval: float = 1.0,
        stats: Optional[ObservationStats] = None,
        pricer: Optional[Pricer] = None,
    ):
        self.dao = dao
        self.stats = stats if stats is not None else ObservationStats()
        # T1.8 成本固化钩子（D7）：None = 默认 unpriced 占位；P10.3 T3.2 注入 yaml 计算器
        self._pricer = pricer
        # P10.1 T1.7：熔断事件环形缓冲（调试/告警消费前兜底可见；T4.2 告警消费接此处）
        self.recent_breaker_events: Deque[BreakerEvent] = deque(maxlen=50)
        self.queue = ObservationQueue(
            self._flush_events,
            maxsize=maxsize,
            flush_count=flush_count,
            flush_interval=flush_interval,
            stats=self.stats,
        )

    # ===================== 发射端（T1.3 起调用；D2 非阻塞契约） =====================

    def emit(self, event: CallEvent) -> bool:
        """非阻塞投递（put_nowait 语义：满即丢并计数，绝不 await）"""
        return self.queue.put_nowait(event)

    # ===================== flush（off-loop 批量落库） =====================

    async def _flush_events(self, batch: List[Any]) -> None:
        await asyncio.to_thread(self._insert_batch, batch)

    def _insert_batch(self, batch: List[Any]) -> None:
        for item in batch:
            if isinstance(item, CallEvent):
                self.dao.insert(self._fix_cost(item.to_row()))
            elif isinstance(item, BreakerEvent):
                # T1.7：熔断事件不入库（无表），进环形缓冲供调试 + 计数；
                # T4.2 在此处接告警分发
                self.recent_breaker_events.append(item)
                self.stats.breaker_events += 1
            else:
                raise ValueError(f"未知观测事件类型，整批拒收: {type(item).__name__}")

    # ===================== 成本固化（T1.8，D7：P10.3 接管前 unpriced 占位） =====================

    def _fix_cost(self, row: Dict) -> Dict:
        """落库前的成本固化点：注入 pricer 优先，否则 unpriced 占位（快照始终写）。

        钩子异常吞掉并计数（执行纪律 4），失败行照常落库并兜底 unpriced 快照——
        「可观测的丢」同样适用于固化失败：pricing_errors 进 /metrics。
        """
        try:
            (self._pricer if self._pricer is not None else apply_unpriced)(row)
        except Exception:  # noqa: BLE001 — 观测故障不得反噬业务（D2），失败可见可计数
            self.stats.pricing_errors += 1
            apply_unpriced(row)  # 兜底占位：固化点故障也不留 NULL 快照
        return row

    # ===================== 生命周期（factory lifespan 调用） =====================

    def start(self) -> None:
        """启动后台 flush 循环（幂等；先于任何发射源）。

        特意做成同步：内部只是 create_task，无可等待操作——async 包装会制造
        「忘 await 即静默不启动」的陷阱（T1.2 测试中的 RuntimeWarning 实证）。
        """
        self.queue.start()

    async def stop_and_drain(self) -> None:
        """D2 停机顺序中的观测步骤：先停生产者 → 本方法 drain → 再释放 DB（container.aclose）"""
        await self.queue.stop_and_drain()

    # ===================== /metrics 序列化出口（D13） =====================

    def metrics(self) -> Dict[str, Any]:
        """/metrics 的观测区块（采集与输出分离：只读 stats/depth，不触发任何行为）"""
        snapshot = self.stats.snapshot()
        snapshot["queue_depth"] = self.queue.depth()
        snapshot["recent_breaker_events"] = len(self.recent_breaker_events)
        return {"observation": snapshot}
