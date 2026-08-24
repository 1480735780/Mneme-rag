# -*- coding: utf-8 -*-
"""
common.idempotent.submit - 防重复提交幂等装饰器（对应 Java @IdempotentSubmit + IdempotentSubmitAspect）

@idempotent_submit(key=..., message=...) 包装业务写方法：
    - async 函数：复用 rag.service.idempotent.IdempotentSubmitGuard（CacheManager 上的
      get+set 模拟 setnx，TTL 锁），重复提交 raise ClientException(message)；
    - sync 函数：进程内 threading.Lock 非阻塞 tryAcquire（对齐 RLock.tryLock 语义），
      重复提交 raise ClientException(message)；finally 释放。

key 解析（对齐 Java buildLockKey 双分支，多一级 key_fn）：
    - 显式 key：`idempotent-submit:key:{key}`（对齐 SpEL key 分支）；
    - key_fn 提取器：接收 (args, kwargs) 返回稳定业务键（如 username），同走 `idempotent-submit:key:{value}`，
      避免默认分支里 bound self 导致跨实例 key 漂移；
    - 未提供：`idempotent-submit:path:{module.qualname}:currentUserId:anonymous:md5:{args md5}`
      （对齐默认分支 path:currentUserId:md5）。

Guard 注入：宿主（wiring 阶段）调 set_guard() 注册容器级实例（与 chat 域共享同一 cache）；
未注册时 get_guard() 懒建内存兜底（MemoryCacheManager），保证装饰器立即可用。
sync 锁字典按业务 key 分组、常驻（key 集合有限，如业务操作标识），不做 GC。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentSubmit
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentSubmitAspect
    - com.nageoffer.ai.ragent.rag.controller.RAGChatController（chat/stop 幂等注解使用点）
"""
from __future__ import annotations

import asyncio
import threading
from functools import wraps
from typing import Any, Callable, Dict, Optional

from common.exception.business import ClientException

# 缺省幂等失败提示（对齐 Java IdempotentSubmit.message 缺省）
DEFAULT_SUBMIT_MESSAGE = "您操作太快，请稍后再试"
# 缺省锁 TTL（秒，仅懒建兜底 guard 时生效；注入的 guard 用其自身配置）
DEFAULT_TTL_SECONDS = 10.0

# 全局注入槽（对齐 audit.support.decorator.set_record_service 注册模式）
_guard: Optional[Any] = None

# sync 路径的进程内互斥锁池（按解析后的 lock key 分组）
_SYNC_LOCKS: Dict[str, threading.Lock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()


def set_guard(guard: Optional[Any]) -> None:
    """注册幂等守卫（wiring 阶段注入容器级实例；传 None 解除注册，用于测试隔离/回落兜底）"""
    global _guard
    _guard = guard


def get_guard(ttl: Optional[float] = None) -> Any:
    """取全局幂等守卫；未注册 → 懒建内存兜底（保证装饰器立即可用）"""
    global _guard
    if _guard is None:
        from rag.service.idempotent import IdempotentSubmitGuard
        from storage.cache import MemoryCacheManager

        _guard = IdempotentSubmitGuard(
            cache=MemoryCacheManager(), ttl=ttl if ttl is not None else DEFAULT_TTL_SECONDS
        )
    return _guard


def idempotent_submit(
    key: Optional[str] = None,
    key_fn: Optional[Callable[[tuple, dict], Any]] = None,
    message: Optional[str] = None,
    ttl: Optional[float] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """防重复提交幂等装饰器（async / sync 双兼容）

    Args:
        key:     显式幂等键（对齐 Java @IdempotentSubmit.key），优先级最高
        key_fn:  key 提取器（接收 (args, kwargs)，返回稳定幂等键）；当 key 为空时启用，
                 用于从请求参数提取业务键（如 username），避免默认「签名+参数 md5」里
                 bound self 导致跨实例 key 漂移
        message: 重复提交时的错误提示（缺省「您操作太快，请稍后再试」）
        ttl:     懒建兜底 guard 的锁 TTL（秒）；注入 guard 时忽略
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            guard = get_guard(ttl)
            lock_key = _resolve_lock_key(func, key, key_fn, args, kwargs)
            # execute：重复提交 raise ClientException(message)；执行 body；finally 释放
            return await guard.execute(
                lock_key, lambda: func(*args, **kwargs), message or DEFAULT_SUBMIT_MESSAGE
            )

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            lock_key = _resolve_lock_key(func, key, key_fn, args, kwargs)
            lock = _sync_lock_for(lock_key)
            # 非阻塞 tryAcquire：获取失败 = 已持有锁 = 重复提交（对齐 RLock.tryLock）
            if not lock.acquire(blocking=False):
                raise ClientException(message or DEFAULT_SUBMIT_MESSAGE)
            try:
                return func(*args, **kwargs)
            finally:
                lock.release()

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# ------------------------------------------------------------------ #
# key 解析（对齐 Java buildLockKey 双分支）
# ------------------------------------------------------------------ #


def _resolve_lock_key(
    func: Callable[..., Any],
    key: Optional[str],
    key_fn: Optional[Callable[[tuple, dict], Any]],
    args: tuple,
    kwargs: dict,
) -> str:
    if key:
        return _value_key(key)
    if key_fn is not None:
        return _value_key(key_fn(args, kwargs))
    path = f"{func.__module__}.{func.__qualname__}"
    return _args_key(path, *args)


def _value_key(value: Any) -> str:
    """SpEL key 分支：idempotent-submit:key:{value}"""
    from rag.service.idempotent import IdempotentSubmitGuard

    return IdempotentSubmitGuard.build_value_key(value)


def _args_key(path: str, *args: Any) -> str:
    """默认分支：idempotent-submit:path:{path}:currentUserId:anonymous:md5:{args md5}"""
    from rag.service.idempotent import IdempotentSubmitGuard

    return IdempotentSubmitGuard.build_args_key(path, "anonymous", *args)


def _sync_lock_for(key: str) -> threading.Lock:
    """取/建 key 对应的进程内互斥锁（sync 路径专用；常驻不清理，key 集合有限）"""
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[key] = lock
        return lock
