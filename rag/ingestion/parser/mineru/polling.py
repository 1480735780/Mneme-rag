"""
MinerU 任务轮询执行器（对应 ragent MinerUPollingExecutor）

Java 用 ScheduledExecutorService 4 线程调度 + CompletableFuture；Python 侧用 asyncio 协程天然异步，
无需线程池即可支撑大批并发任务（不占用任何阻塞线程）。

语义对齐：
    - DONE  → 返回状态
    - FAILED → 抛 ServiceException（携带 err_msg）
    - 超时   → 抛 ServiceException（等待超时）
    - 瞬时网络错误 → 记日志并继续轮询至 deadline（单点抖动不误杀）
"""
from __future__ import annotations

import logging
import time

from common.exception.business import ServiceException
from rag.ingestion.parser.mineru.client import MinerUClient
from rag.ingestion.parser.mineru.model import MinerUStatus
from rag.ingestion.parser.mineru.properties import MinerUProperties

logger = logging.getLogger(__name__)


class MinerUPollingExecutor:
    def __init__(self, client: MinerUClient, properties: MinerUProperties):
        self._client = client
        self._properties = properties

    async def submit_and_await(self, batch_id: str) -> MinerUStatus:
        if not batch_id or not batch_id.strip():
            raise ServiceException("MinerU batchId 不能为空")
        timeout_seconds = max(1, self._properties.timeout_seconds)
        interval = max(0.1, self._properties.poll_interval_seconds)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                status = await self._client.query_result(batch_id)
            except Exception as e:  # 瞬时网络/解析错误：继续轮询至 deadline
                if time.monotonic() >= deadline:
                    raise ServiceException(f"MinerU 轮询持续失败到超时 batchId={batch_id}: {e}") from e
                logger.warning("MinerU 轮询瞬时错误，重试 batchId=%s: %s", batch_id, e)
                await asyncio_sleep(interval)
                continue
            if status.completed():
                return status
            if status.failed():
                raise ServiceException(
                    f"MinerU 任务失败 batchId={batch_id} err={status.error_message}"
                )
            if time.monotonic() >= deadline:
                raise ServiceException(f"MinerU 任务等待超时 batchId={batch_id}")
            await asyncio_sleep(interval)


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
