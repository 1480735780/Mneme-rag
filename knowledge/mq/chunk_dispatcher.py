# -*- coding: utf-8 -*-
"""
knowledge.mq.chunk_dispatcher - 分块任务异步分发（对应 Java 事务消息 + Consumer 的语义等价）

Java 用 RocketMQ 事务消息：本地事务（CAS 状态 + upsertSchedule）成功才投递，Consumer 异步执行
executeChunk。单进程内用「先同步 CAS、成功后 asyncio.create_task」等价替代——事务消息本质是
「本地事务成功才投递」，CAS 成功后 create_task 语义一致（R1 决策）；幂等由 CAS 守门保证（重投
同 doc 的 CAS 失败报错），create_task 本身非幂等。跨实例部署时 P6 换 Redis Stream/消息队列实现，
消费方接口不变。

闸门：execute_chunk 包一层 asyncio.Semaphore（max_concurrent_chunks，默认 2），防止同时分块
太多打爆嵌入服务（对齐 Java knowledgeChunkExecutor 线程池 + 信号量语义，R10）。

对应 ragent 源码：
    - knowledge/mq/KnowledgeDocumentChunkEvent + Consumer + TransactionChecker（R1 合并）
    - knowledge/service/impl/KnowledgeDocumentServiceImpl#startChunk（事务体 CAS 语义）
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkTaskEvent:
    """分块任务事件（对应 Java KnowledgeDocumentChunkEvent；operator 用于落 created_by/updated_by）"""

    doc_id: str
    operator: Optional[str] = None


class ChunkTaskDispatcher(ABC):
    """分块任务分发抽象：CAS 成功后才真正异步执行已经向量化的执行体（P6 可换 Redis Stream 实现）"""

    @abstractmethod
    async def dispatch(self, event: ChunkTaskEvent) -> None:
        """同步 CAS 事务体并投递异步执行；CAS 失败抛 ClientException，不投递"""
        ...


class ProcessChunkTaskDispatcher(ChunkTaskDispatcher):
    """进程内异步分发（对应 Java 事务消息 + Consumer 语义等价）

    Args:
        start_chunk:  同步事务体（CAS status ne RUNNING→RUNNING + upsertSchedule）。
                      签名 ``(doc_id, operator) -> None``；文档不存在或已在分块时抛 ClientException。
                      其返回等价于「本地事务提交成功」，此后才投递异步执行。
        execute_chunk: 异步执行体（即 document_service.execute_chunk），签名 ``(doc_id) -> Awaitable``。
        max_concurrent: 分块执行并发闸门（asyncio.Semaphore 许可数，默认 2，为 Python 侧简化——
                       Java 用 CPU 推导的 knowledgeChunkExecutor 线程池，无 @Value 键，见偏离说明）。
    """

    def __init__(
        self,
        start_chunk: Callable[[str, Optional[str]], None],
        execute_chunk: Callable[[str], Any],
        max_concurrent: int = 2,
    ):
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent 必须 >= 1，实际 {max_concurrent}")
        self._start_chunk = start_chunk
        self._execute_chunk = execute_chunk
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # CPython 事件循环对 create_task 只持弱引用：分块是等信号量/嵌入服务的长任务，必须持有强引用避免中途消失
        self._tasks: set = set()

    async def dispatch(self, event: ChunkTaskEvent) -> None:
        # 事务体：CAS 更新成功才返回；已在进行中抛「分块操作正在进行中」→ 不投递、上层透传 400
        self._start_chunk(event.doc_id, event.operator)
        # 本地事务成功后投递：异步执行分块（任务异常在 _run 内兜底，不污染调用方事件循环）。
        # 幂等来自 CAS 守门（重在 RUNNING 后同 doc 不可再投递），create_task 本身非幂等。
        task = asyncio.create_task(self._run(event.doc_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, doc_id: str) -> None:
        """带信号量闸门的后台执行；异常统一记日志（对齐风险应对：后台任务异常不静默丢失）"""
        async with self._semaphore:
            try:
                await self._execute_chunk(doc_id)
            except Exception:  # noqa: BLE001 —— 后台任务兜底，状态回写由 execute_chunk 内部负责。
                # 刻意不捕 CancelledError（BaseException 子类，这里本就捕不到）：任务取消应当传播，勿扩成 except BaseException。
                logger.exception("分块异步执行失败：docId=%s", doc_id)