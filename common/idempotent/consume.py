# -*- coding: utf-8 -*-
"""
common.idempotent.consume - 消费幂等装饰器（对应 Java @IdempotentConsume + IdempotentConsumeAspect + IdempotentConsumeStatusEnum）

防止消息消费者重复消费：以「状态令牌」判定
    - CONSUMING("0")：消费中 → 重复消费，raise ClientException（等待延迟重试）；
    - CONSUMED("1")：已完成 → 直接跳过（返回 None）；
    - 无状态：置 CONSUMING → 执行 body → 置 CONSUMED；执行异常 → 删除令牌（可重试）。

对齐 Java Lua `SET key value NX GET PX expire_ms` 语义：R-C 起 CacheManager 提供
set_if_absent 原子原语（Redis SET NX EX / Memory 实例锁），首占走原子写入——
并发双消费只有一方能置 CONSUMING，另一方读到 CONSUMING 拒绝（此前 get+set 模拟
存在双消费者同时置位的竞态窗口，R-C 销案）。

key 解析（对齐 Java keyPrefix + SpEL key，Python 用 key_fn 等价）：
    - key：显式防重令牌键（与 key_prefix 拼接）；
    - key_fn：(args, kwargs) → 稳定业务键（替代 SpEL 表达式）；
    - 均未提供：回落 func 签名 + 参数 md5 的稳定键。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentConsume
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentConsumeAspect
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentConsumeStatusEnum
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional

from common.exception.business import ClientException
from storage.cache import CacheManager, MemoryCacheManager

# 缺省防重令牌 TTL（秒，对齐 Java keyTimeout 默认 3600）
DEFAULT_KEY_TIMEOUT = 3600

# 全局注入槽（对齐 audit/support/decorator 注册模式）
_guard: Optional["IdempotentConsumeGuard"] = None


class IdempotentConsumeStatus(Enum):
    """幂等 MQ 消费状态（对齐 Java IdempotentConsumeStatusEnum）"""

    CONSUMING = "0"  # 消费中
    CONSUMED = "1"  # 已消费

    @classmethod
    def is_error(cls, code: Optional[str]) -> bool:
        """消费中视为失败（对齐 Java isError）"""
        return code == cls.CONSUMING.value


class IdempotentConsumeGuard:
    """消费幂等守卫（对应 Java IdempotentConsumeAspect 核心逻辑）"""

    def __init__(
        self,
        cache: Optional[CacheManager] = None,
        key_timeout: float = DEFAULT_KEY_TIMEOUT,
    ):
        self._cache: CacheManager = cache or MemoryCacheManager()
        self._key_timeout = key_timeout

    async def consume(
        self,
        key: str,
        fn: Callable[[], Any],
        async_fn: bool = False,
    ) -> Any:
        """幂等消费：原子占位 CONSUMING → 执行 → 置 CONSUMED；CONSUMING 拒绝 / CONSUMED 跳过

        对齐 Java Lua SET NX GET：set_if_absent 失败（键已存在）后读旧值分流——
        CONSUMING 抛错（重复消费）、CONSUMED 跳过返回 None。

        async_fn=True 时 fn 为协程函数（await 执行）；否则 fn 为普通可调用。
        """
        acquired = await self._cache.set_if_absent(key, IdempotentConsumeStatus.CONSUMING.value, ttl=self._key_timeout)
        if not acquired:
            current = await self._cache.get(key)
            if current == IdempotentConsumeStatus.CONSUMED.value:
                return None
            raise ClientException(f"消息消费者幂等异常，幂等标识：{key}")
        try:
            result = await fn() if async_fn else fn()
        except Exception:
            await self._cache.delete(key)
            raise
        await self._cache.set(key, IdempotentConsumeStatus.CONSUMED.value, ttl=self._key_timeout)
        return result


def set_guard(guard: Optional[IdempotentConsumeGuard]) -> None:
    """注册全局消费幂等守卫（wiring 注入；None 解除用于测试隔离）"""
    global _guard
    _guard = guard


def get_guard(key_timeout: float = DEFAULT_KEY_TIMEOUT) -> IdempotentConsumeGuard:
    """取全局守卫；未注册 → 懒建内存兜底（保证装饰器立即可用）"""
    global _guard
    if _guard is None:
        _guard = IdempotentConsumeGuard(
            cache=MemoryCacheManager(), key_timeout=key_timeout
        )
    return _guard


def _args_md5(*args) -> str:
    """参数稳定序列化 md5（对齐 submit 的 _args_md5）"""
    payload = json.dumps(list(args), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def _resolve_consume_key(
    func: Callable[..., Any],
    key: Optional[str],
    key_fn: Optional[Callable[[tuple, dict], Any]],
    args: tuple,
    kwargs: dict,
) -> str:
    if key:
        return key
    if key_fn is not None:
        value = key_fn(args, kwargs)
        return str(value)
    path = f"{func.__module__}.{func.__qualname__}"
    return f"{path}:md5:{_args_md5(*args)}"


def idempotent_consume(
    key_prefix: str = "",
    key: Optional[str] = None,
    key_fn: Optional[Callable[[tuple, dict], Any]] = None,
    key_timeout: float = DEFAULT_KEY_TIMEOUT,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """消费幂等装饰器（async / sync 双兼容）

    Args:
        key_prefix:  防重令牌 key 前缀（对齐 Java keyPrefix，默认空）
        key:         显式防重令牌键（对齐 Java key，默认空走 key_fn/签名兜底）
        key_fn:      (args, kwargs) → 稳定业务键（替代 Java SpEL 表达式）
        key_timeout: 令牌过期秒数（对齐 Java keyTimeout，默认 3600）
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            guard = get_guard(key_timeout)
            full_key = key_prefix + _resolve_consume_key(func, key, key_fn, args, kwargs)
            return await guard.consume(full_key, lambda: func(*args, **kwargs), async_fn=True)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            # sync 路径：与 async 路径共用同一套 cache 状态机（set_if_absent 原子占位，R-C）。
            # CacheManager 为 async 接口，以 asyncio.run 桥接（sync 函数仅在无运行中事件循环的
            # 线程被调用，桥接安全）；状态令牌跨 sync/async 一致，跨进程由 P6 real 栈 Redis 兜底。
            guard = get_guard(key_timeout)
            full_key = key_prefix + _resolve_consume_key(func, key, key_fn, args, kwargs)
            return asyncio.run(
                guard.consume(full_key, lambda: func(*args, **kwargs), async_fn=False)
            )

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator
