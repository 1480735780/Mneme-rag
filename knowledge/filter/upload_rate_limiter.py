# -*- coding: utf-8 -*-
"""
knowledge.filter.upload_rate_limiter - 文件上传并发闸门（对应 Java UploadRateLimitFilter，R8 -> service 层）

Java 是 Servlet Filter + Redisson 信号量（rag:document:upload，maxConcurrent=10、maxWait=30s、lease=30s），
在 multipart 解析前拦截；Python 无 Filter 链，按 R8 决策下沉为 service 层 Injectable 的 `UploadRateLimiter`。

语义对齐：
    - 过载（超 MaxWaitSeconds 未拿到许可）→ `TooManyRequestsException`（全局处理器映射 A000429，对齐 Java 429 JSON）
    - 进程内用 asyncio.Semaphore（Python ≥3.10 构造不绑 loop，DI 单例安全；单实例语义等价）
    - P6 多实例换 Redis FairRateLimiter 时，针对其客户端异常再补 fail-open（当前进程内 Semaphore 无此需求，
      刻意不写 `except Exception` 兜底，避免把编程错误静默转成「不限流」）

对应 ragent 源码：
    - knowledge/filter/UploadRateLimitFilter（doFilterInternal 的 tryAcquire/release 语义）
    - knowledge/config/RagSemaphoreProperties（documentUpload maxConcurrent/maxWaitSeconds/leaseSeconds）
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from common.exception.business import TooManyRequestsException


class _UploadPermit:
    """已获取的并发许可；释放时归还信号量"""

    __slots__ = ("_semaphore",)

    def __init__(self, semaphore: asyncio.Semaphore):
        self._semaphore = semaphore

    async def __aenter__(self) -> "_UploadPermit":
        return self

    async def __aexit__(self, *exc_info) -> None:
        self._semaphore.release()


class UploadRateLimiter:
    """上传并发闸门（进程内 Semaphore + 超时）"""

    def __init__(self, max_concurrent: int = 10, max_wait_seconds: float = 30.0):
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent 必须 >= 1，实际 {max_concurrent}")
        if max_wait_seconds <= 0:
            raise ValueError(f"max_wait_seconds 必须 > 0，实际 {max_wait_seconds}")
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_wait_seconds = max_wait_seconds

    @asynccontextmanager
    async def limit(self) -> AsyncIterator[None]:
        """限流上下文：`async with limiter.limit():` 包住上传；过载抛 TooManyRequestsException（对齐 Java 429）"""
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self._max_wait_seconds)
        except asyncio.TimeoutError:
            raise TooManyRequestsException("当前上传人数过多，请稍后再试")  # 对齐 Java 429
        async with _UploadPermit(self._semaphore):
            yield None