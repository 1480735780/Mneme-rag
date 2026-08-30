# -*- coding: utf-8 -*-
"""
agent.run_gate - Agent 并发闸门（对应 Java AgentRunGate）

每用户同时只允许一个运行中的 Agent 会话：acquire 失败即拒绝（"当前会话处理中"），
被拒的请求不该留下任何副作用（META 事件 / 会话行 / 任务登记都发生在 acquire 之后）。

与 Java 的差异（有意适配）：Java 用 Redisson setIfAbsent（跨节点原子）；house CacheManager
只有 get/set/delete（无 setnx），本节点以「进程内 per-user 锁串行化 check-then-set」保证
单节点无竞态，槽值经 cache 落地供 running_task_id 探询与多节点可见；多节点部署的原子
setnx 需后续把 CacheManager 扩展 set_if_absent（StreamTaskManager 跨节点广播已随 P3-2 交付，
本项仍挂账）。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.service.handler.AgentRunGate
"""
from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable, Dict, Optional

from common.exception.business import ClientException
from storage.cache import CacheManager

RUNNING_KEY_PREFIX = "ragent:agent:running:"
SLOT_SEPARATOR = "|"


class AgentRunGate:
    """每用户单运行槽（异步接口；CacheManager 为异步契约）"""

    def __init__(self, cache: CacheManager, sse_timeout_ms: int):
        self._cache = cache
        self._ttl_seconds = sse_timeout_ms * 2 / 1000.0
        self._registry_lock = threading.Lock()
        self._local_locks: Dict[str, asyncio.Lock] = {}

    async def acquire(self, user_id: str, task_id: str, conversation_id: str) -> Callable[[], Awaitable[None]]:
        """
        占用运行槽；被占用时抛 ClientException。返回释放钩子（值比对防误删新槽）。

        闸门先于一切副作用：调用方必须在 acquire 成功后才发 META / 落会话行 / 登记任务。
        """
        slot_value = f"{task_id}{SLOT_SEPARATOR}{conversation_id}"
        key = self._running_key(user_id)
        async with self._lock_for(user_id):
            existing = await self._cache.get(key)
            if existing:
                raise ClientException("当前会话处理中，请稍后再发起新的对话")
            await self._cache.set(key, slot_value, ttl=self._ttl_seconds)

        async def release() -> None:
            current = await self._cache.get(key)
            if current == slot_value:  # CAS 语义：不误删后继运行写入的新槽
                await self._cache.delete(key)

        return release

    async def running_task_id(self, user_id: str, conversation_id: str) -> Optional[str]:
        """查询该用户当前运行中的任务 ID（仅当槽值会话与本会话一致时返回；对齐 Java）"""
        slot_value = await self._cache.get(self._running_key(user_id))
        if not slot_value:
            return None
        separator = slot_value.find(SLOT_SEPARATOR)
        if separator < 0 or slot_value[separator + 1:] != conversation_id:
            return None
        return slot_value[:separator]

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        with self._registry_lock:
            lock = self._local_locks.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._local_locks[user_id] = lock
            return lock

    def _running_key(self, user_id: str) -> str:
        return f"{RUNNING_KEY_PREFIX}{user_id}"
