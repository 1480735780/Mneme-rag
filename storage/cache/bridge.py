# -*- coding: utf-8 -*-
"""
同步 / 异步缓存桥（供同步 Face 的缓存管理器驱动异步 CacheManager）

storage.cache.CacheManager 是 asyncio 接口；而 rag/ 侧若干缓存管理器
（AgentPromptCacheManager、IntentTreeCacheManager …）在异步链路中被同步调用
（引擎流式链路跑在事件循环线程内）。AsyncCacheBridge 以私有事件循环线程承载
协程并阻塞等待结果，对应 Java StringRedisTemplate 在请求线程内的阻塞语义——
因此在任何线程中调用均安全，不能用 asyncio.run（运行中的循环内会抛错）。

对应 ragent 源码：
    - 各 CacheManager（StringRedisTemplate 阻塞 I/O）的 Python 等价桥接
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional


class AsyncCacheBridge:
    """同步调用桥：私有事件循环线程承载异步协程并阻塞等待结果"""

    _lock = threading.Lock()
    _loop: Optional[asyncio.AbstractEventLoop] = None

    @classmethod
    def run(cls, coro) -> Any:
        with cls._lock:
            if cls._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever, name="cache-sync-bridge", daemon=True
                ).start()
                cls._loop = loop
        return asyncio.run_coroutine_threadsafe(coro, cls._loop).result()