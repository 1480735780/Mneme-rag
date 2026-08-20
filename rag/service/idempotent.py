# -*- coding: utf-8 -*-
"""
rag.service.idempotent - 防重复提交幂等守卫（对应 Java @IdempotentSubmit + IdempotentSubmitAspect）

基于 CacheManager 的「get 存在性检查 + set 写入模拟 setnx（TTL）」实现，缺口语义对齐 Java
RLock.tryLock()：获取失败=已持有锁=重复提交 → raise ClientException(message)；finally 释放。

- build_args_key：对齐 Java 默认 lock key idempotent-submit:path:path:currentUserId:userId:md5:args
  （args 经 JSON 稳定序列化后 md5，对齐 Java gson.toJson + md5Hex）；
- build_value_key：对齐 Java SpEL key 分支 idempotent-submit:key:value；
- acquire/release：获取/释放幂等锁（TTL 缺省 10s）；
- execute：幂等包裹执行——重复提交 raise ClientException，正常执行 body 且 finally 释放。

CacheManager 抽象仅提供 get/set/delete（无原子 setnx，计划 D 决策），故以 get+set 模拟；
get 契约 miss/失败均返回 None（RedisCacheManager 异常兜底返回 None，非 false），acquire 只认定
自有标记 True——兜底时宁可放行不阻断请求。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentSubmit
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentSubmitAspect
    - com.nageoffer.ai.ragent.rag.controller.RAGChatController（chat/stop 幂等注解）
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from common.exception.business import ClientException
from storage.cache import CacheManager, MemoryCacheManager

logger = logging.getLogger(__name__)

# 缺省幂等失败提示（对齐 Java IdempotentSubmit.message 缺省）
DEFAULT_SUBMIT_MESSAGE = "您操作太快，请稍后再试"
# 缺省锁 TTL（秒）
DEFAULT_TTL_SECONDS = 10.0
# chat/stop 专用提示（对齐 RAGChatController.chat 注解 message）
CHAT_SUBMIT_MESSAGE = "当前会话处理中，请稍后再发起新的对话"


def _args_md5(*args) -> str:
    """对齐 Java gson.toJson(args) + md5Hex：JSON 稳定序列化参数后取 md5"""
    payload = json.dumps(list(args), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.md5(payload).hexdigest()


class IdempotentSubmitGuard:
    """防重复提交守卫（对应 Java IdempotentSubmitAspect）"""

    def __init__(
        self,
        cache: Optional[CacheManager] = None,
        ttl: float = DEFAULT_TTL_SECONDS,
    ):
        self._cache: CacheManager = cache or MemoryCacheManager()
        self._ttl = ttl

    # ==================== 键构建 ====================

    @staticmethod
    def build_args_key(path: str, user_id: Optional[str], *args) -> str:
        """默认分支 lock key（对齐 Java buildLockKey：path:currentUserId:md5）"""
        return f"idempotent-submit:path:{path}:currentUserId:{user_id}:md5:{_args_md5(*args)}"

    @staticmethod
    def build_value_key(value: Any) -> str:
        """SpEL key 分支 lock key（对齐 Java idempotent-submit:key:{value}）"""
        return f"idempotent-submit:key:{value}"

    # ==================== 锁操作 ====================

    async def acquire(self, key: str) -> bool:
        """获取幂等锁：已持有（get 返回自有标记 True）→ False（重复）；否则 set(TTL) 模拟 setnx → True

        miss(None) / 后端失败(None) / 非自有值均视为未持有——只认定本守卫写入的 True，
        避免「is not None」把失败/其它缓存值误判为持有而拒绝请求（对齐 CacheManager.get 契约：失败→None）。
        """
        if await self._cache.get(key) is True:
            return False
        await self._cache.set(key, True, ttl=self._ttl)
        return True

    async def release(self, key: str) -> None:
        """释放幂等锁（幂等）"""
        try:
            await self._cache.delete(key)
        except Exception:  # noqa: BLE001 —— 释放失败静默（对齐 RedisCacheManager 兜底）
            logger.warning("幂等锁释放失败，key=%s", key, exc_info=True)

    # ==================== 幂等包裹 ====================

    async def execute(
        self,
        key: str,
        fn: Callable[[], Awaitable[Any]],
        message: Optional[str] = None,
    ) -> Any:
        """幂等包裹执行：重复提交 raise ClientException(message)；正常执行 body；finally 释放"""
        if not await self.acquire(key):
            raise ClientException(message or DEFAULT_SUBMIT_MESSAGE)
        try:
            return await fn()
        finally:
            await self.release(key)