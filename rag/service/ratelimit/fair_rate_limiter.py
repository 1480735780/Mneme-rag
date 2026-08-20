# -*- coding: utf-8 -*-
"""
rag.service.ratelimit.fair_rate_limiter - 公平限流器（6.2 进程内版 + 6.3 Redis 版，同 ABC）

接口（async，对齐 Java FairDistributedRateLimiter 的 Ticket 语义）：
    FairRateLimiter.acquire(max_wait_seconds) -> Permit
        - 成功 → Permit（async 上下文管理器，try/finally 释放，防泄漏）；
        - 排队超时 → 抛 `RateLimitTimeout`（对齐 TIMED_OUT）；
        - 调用方取消 → asyncio.CancelledError 上抛（对齐 CANCELLED），finally 兜底清理无泄漏；
        - `enabled=False` → 直接放行（限流关闭直通）。

**fail-open 策略（6.2 定案，缺省 fail-open）**：
    限流器**内部意外异常**（非超时、非取消）一律放行（返回 NoopPermit）并打 warn——
    故障时放行用户流量（宁可不限流，不让聊天被误锁死），与 6.1 `global_enabled` 缺省 True 配合
    成「默认限流、异常时熔断放行」。超时/取消是**合法拒绝/取消路径**，不属于 fail-open。

实现：
    - ProcessFairRateLimiter（6.2，单机够用）：复用 asyncio.Semaphore 的**内建 FIFO 公平性**，
      wait_for 提供超时；lease/poll 仅接口对齐保留（async 释放天然闭环）。
    - RedisFairRateLimiter（6.3，分布式）：ZSet FIFO + Lua 原子判头；entry **三态生命周期**
      （等待 TTL=预算+poll+buffer / 持有 TTL=lease / 删除）+ Lua 死头清扫与过期持有回收。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.ratelimit.FairDistributedRateLimiter（语义对齐）
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lua 原子判头脚本（对齐 Java lua/queue_claim_atomic.lua）
_LUA_PATH = Path(__file__).parent / "lua" / "queue_claim_atomic.lua"
# Lua 原子条件释放脚本（6.3 返工：真实持释放才 INCR，消除无条件 INCR 超发 + 三跳非原子半态）
_RELEASE_LUA_PATH = Path(__file__).parent / "lua" / "permit_release_atomic.lua"
# entry 存活标记额外缓冲（防毫秒级时钟漂移误杀存活条目）；等待态 TTL 还会叠加 poll_interval
_ENTRY_TTL_BUFFER_MS = 5_000


class RateLimitError(RuntimeError):
    """限流基类异常"""


class RateLimitTimeout(RateLimitError):
    """排队超时（对齐 Java TIMED_OUT）"""


class Permit:
    """占用的许可：async 上下文管理器，finally 自动归还（对齐 Java grant + releaseHeldPermit）"""

    async def release(self) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> "Permit":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.release()


class _NoopPermit(Permit):
    """限流关闭 / fail-open 直通许可：不占用任何资源"""

    async def release(self) -> None:
        return None

    async def __aenter__(self) -> "_NoopPermit":
        return self


class FairRateLimiter(ABC):
    """公平限流器抽象接口（进程内 / Redis 分布式共用）"""

    @abstractmethod
    async def acquire(self, max_wait_seconds: Optional[float] = None) -> Permit:
        """排队抢占许可；超时抛 RateLimitTimeout，取消上抛 CancelledError，失败放行（fail-open）"""

    @abstractmethod
    async def close(self) -> None:
        """释放限流器资源（进程内为空操作；Redis 版标记关闭）"""


class ProcessFairRateLimiter(FairRateLimiter):
    """进程内公平限流器（6.2，对应 Java 单机等价语义）

    Args:
        max_concurrent:     最大并发许可数（≥1，由 6.1 RateLimitProperties 校验保证）
        default_wait_seconds: 缺省排队等待秒数（未显式给 timeout 时用；对齐 global_max_wait_seconds）
        enabled:            是否启用限流；False → 直通（对齐 global_enabled）
        fail_open:          内部意外异常是否放行（缺省 True，见模块 fail-open 策略）
        lease_seconds / poll_interval_ms: 仅接口对齐保留（async 释放天然闭环，无需崩溃回收/轮询）
    """

    def __init__(
        self,
        max_concurrent: int,
        *,
        default_wait_seconds: float = 20,
        enabled: bool = True,
        fail_open: bool = True,
        lease_seconds: int = 600,
        poll_interval_ms: int = 200,
    ):
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent 必须 ≥ 1，当前：{max_concurrent}")
        self._max_concurrent = max_concurrent
        self._default_wait_seconds = default_wait_seconds
        self._enabled = enabled
        self._fail_open = fail_open
        self._sem = asyncio.Semaphore(max_concurrent)
        self._closed = False

    async def acquire(self, max_wait_seconds: Optional[float] = None) -> Permit:
        if not self._enabled or self._closed:
            return _NoopPermit()  # 限流关闭 / 已关闭：直通（fail-open 语义）
        timeout = max_wait_seconds if max_wait_seconds is not None else self._default_wait_seconds
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=timeout)
            return _SemPermit(self._sem)
        except asyncio.TimeoutError:
            raise RateLimitTimeout("排队等待超时") from None
        except asyncio.CancelledError:
            raise  # 取消 → CANCELLED，由调用方 finally 兜底
        except Exception as ex:  # noqa: BLE001 —— 内部异常：fail-open 放行，不锁死流量
            if not self._fail_open:
                raise
            logger.warning("限流器内部异常，fail-open 放行: %s", ex, exc_info=True)
            return _NoopPermit()

    async def close(self) -> None:
        self._closed = True  # 进程内无需额外资源；后续 acquire 直通


class _SemPermit(Permit):
    """持有一个 asyncio.Semaphore 许可；release 幂等归还"""

    __slots__ = ("_sem", "_released")

    def __init__(self, sem: asyncio.Semaphore):
        self._sem = sem
        self._released = False

    async def release(self) -> None:
        if not self._released:
            self._released = True
            self._sem.release()


class RedisFairRateLimiter(FairRateLimiter):
    """Redis 分布式公平限流器（6.3，对齐 Java FairDistributedRateLimiter 语义）

    与 6.2 同 `FairRateLimiter` ABC；wiring 按 `rate_limit.backend=process|redis` 切换。

    entry **三态生命周期**（6.3 核验返工，规避死队头/持有期无 lease）：
      - 等待态：entry=`waiting` + PX(等待预算 + poll + buffer)；成员在等待队列 ZSet；
      - 持有态：claim 起在 Lua 内原子改写 entry=`held` + PX(lease_seconds) + 记入持有登记 ZSet；
      - 删除态：release / 取消 / 超时后消失。
    Lua（单次 EVAL，多实例安全）完成：**死队头清扫**（等待态 entry 已过期 → ZREM 弹出）、
    **过期持有回收**（许可耗尽时，持有 ZSet 中 score≤now 的成员归还许可并移除登记）、
    **原子判头 + 扣许可 + 登记持有**。

    注释定的设计取舍：
      - 等待态 TTL = 等待预算 + poll_interval + buffer（poll 可能 > 固定 buffer，需叠加余量）；
      - 排队序号 INCR 与 ZADD 两跳非原子，极罕并发下可能一档越位（公平性微瑕，接受并注释，
        对齐 Java enqueue 亦非单原子）；
      - `close()` 只挡**新 acquire**（直通）——已排队 waiter 不会被动唤醒，各自空转到超时，这是**有意**的；
      - **释放走原子条件脚本**（lua/permit_release_atomic.lua）：`ZREM(held)==1` 才 INCR——
        1:1 上界由「真实持释放」保证（迟到/重复/已回收的 release 不再超发）；判头脚本的过期持有
        回收同样只对 held 中真实成员 INCR，两路不会重复归还；
      - 复用的客户端假体需支持：get/set(nx,px)/incr/decr/delete/zadd/zrem/zrange/zrangebyscore/exists/eval（两类脚本）。

    Args:
        name: 限流器名（作 Redis key 前缀，自动包 {name} 哈希槽保证 Lua 多 key 同槽）
        client: redis.asyncio 兼容客户端（生产 redis.asyncio.Redis；测试注入 fake）
        max_concurrent: 最大并发许可（≥1；start 时 NX 初始化）
        default_wait_seconds / lease_seconds / poll_interval_ms / enabled / fail_open: 同 6.2
        entry_ttl_buffer_ms: 等待态 entry 的固定缓冲；最终 TTL=poll_interval_ms+entry_ttl_buffer_ms
    """

    def __init__(
        self,
        name: str,
        client,
        *,
        max_concurrent: int,
        default_wait_seconds: float = 20,
        lease_seconds: int = 600,
        poll_interval_ms: int = 200,
        enabled: bool = True,
        fail_open: bool = True,
        entry_ttl_buffer_ms: int = _ENTRY_TTL_BUFFER_MS,
    ):
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent 必须 ≥ 1，当前：{max_concurrent}")
        self._name = name
        self._client = client
        self._max_concurrent = max_concurrent
        self._default_wait_seconds = default_wait_seconds
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = max(0.05, poll_interval_ms / 1000.0)
        self._waiting_ttl_ms = poll_interval_ms + entry_ttl_buffer_ms  # 等待态 TTL=预算+poll+buffer
        self._enabled = enabled
        self._fail_open = fail_open
        # key 布局：统一 {name} 哈希槽，保证 Lua 多 key 同槽
        tag = name if ("{" in name and "}" in name) else "{%s}" % name
        self._queue_key = f"{tag}:queue"
        self._held_key = f"{tag}:held"
        self._seq_key = f"{tag}:seq"
        self._permits_key = f"{tag}:permits"
        self._entry_prefix = f"{tag}:entry:"
        self._claim_lua = _load_lua()
        self._release_lua = _load_release_lua()
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """一次性初始化许可计数（NX 幂等），对齐 Java trySetPermits"""
        if self._started:
            return
        await self._client.set(self._permits_key, self._max_concurrent, nx=True)
        self._started = True

    async def acquire(self, max_wait_seconds: Optional[float] = None) -> Permit:
        if not self._enabled or self._closed:
            return _NoopPermit()
        timeout = max_wait_seconds if max_wait_seconds is not None else self._default_wait_seconds
        try:
            await self.start()
            return await asyncio.wait_for(self._do_acquire(timeout), timeout=timeout)
        except RateLimitError:
            raise  # 超时属限流家族，不被 fail-open 吞（6.3 核验 bug 5）
        except asyncio.TimeoutError:
            raise RateLimitTimeout("排队等待超时") from None
        except asyncio.CancelledError:
            raise
        except Exception as ex:  # noqa: BLE001 —— 仅内部意外异常才 fail-open
            if not self._fail_open:
                raise
            logger.warning("Redis 限流器内部异常，fail-open 放行: %s", ex, exc_info=True)
            return _NoopPermit()

    async def _do_acquire(self, timeout: float) -> Permit:
        """入队（三跳 setup 全程在 try 内：取消也走清理，不造死头）→ 轮询判头 → 抢占成功/超时/取消"""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            await self._client.set(self._entry_prefix + request_id, "waiting",
                                   px=timeout * 1000 + self._waiting_ttl_ms)
            seq = await self._client.incr(self._seq_key)
            await self._client.zadd(self._queue_key, {request_id: seq})
            while True:
                if await self._claim_head(request_id):
                    return RedisPermit(self, request_id)  # 成功：不动 entry（held 生命周期由 release/lease 管）
                if loop.time() >= deadline:
                    raise RateLimitTimeout("排队等待超时")
                await asyncio.sleep(self._poll_interval_seconds)
        except (asyncio.CancelledError, RateLimitTimeout):
            await self._cleanup_waiting(request_id)
            raise

    # ==================== Redis 操作 ====================

    async def _cleanup_waiting(self, request_id: str) -> None:
        """取消/超时清理：**优先原子条件释放（若已持 → 立即还槽）**，再出等待队列 + 删 entry（幂等）

        获准后取消的竞态：entry 已为 held（Lua 判头已 grant、协程被取消）——先走释放脚本原子归还
        许可 + 删 entry，避免该槽一直悬挂到 lease 兜底回收；等待态 entry 走同脚本仅清理 entry、不超发。
        """
        try:
            await self._client.eval(
                self._release_lua, 3,
                self._held_key, self._permits_key, self._entry_prefix,
                request_id,
            )
        except Exception:  # noqa: BLE001
            logger.debug("[%s] 条件释放清理失败 request=%s", self._name, request_id, exc_info=True)
        try:
            await self._client.zrem(self._queue_key, request_id)
        except Exception:  # noqa: BLE001
            logger.debug("[%s] 移除队列失败 request=%s", self._name, request_id, exc_info=True)
        try:
            await self._client.delete(self._entry_prefix + request_id)
        except Exception:  # noqa: BLE001 —— 释放脚本已删 entry，此处幂等兜底
            logger.debug("[%s] 删除 entry 失败 request=%s", self._name, request_id, exc_info=True)

    async def _claim_head(self, request_id: str) -> bool:
        """单次 EVAL 原子「清扫死头 + 回收过期持有 + 判头 + 扣许可 + 登记持有」"""
        now = int(time.time())
        result = await self._client.eval(
            self._claim_lua, 4,
            self._queue_key, self._held_key, self._permits_key, self._entry_prefix,
            request_id, now, self._lease_seconds,
        )
        try:
            return result is not None and int(result[0]) == 1
        except Exception:  # noqa: BLE001 —— 解析失败当未抢到，继续轮询（会空转到超时，可接受）
            logger.debug("[%s] claim 结果解析失败 result=%r", self._name, result, exc_info=True)
            return False

    async def _release(self, request_id: str) -> None:
        """原子条件释放：**仅当 held 中真实持有时才 INCR 归还**，并删 entry

        单次 EVAL（lua/permit_release_atomic.lua）：
            - 迟到/重复/已回收的 release → ZREM 返回 0 → 不 INCR（1:1 上界，不超发，修 P-R1）；
            - 三跳合并单脚本 → 父任务在释放中途被取消也不会留下「held 已删、许可未还」半态
              （脚本原子执行，修 6.3 返工 P-Q2 永久 -1）；
            - 返回 1 = 真实持释放；0 = 非持有（调用方一般不消费返回值）。
        """
        try:
            await self._client.eval(
                self._release_lua, 3,
                self._held_key, self._permits_key, self._entry_prefix,
                request_id,
            )
        except Exception as ex:  # noqa: BLE001 —— 归还失败仅告警（原子脚本无半态，lease 兜底回收）
            logger.warning("[%s] 归还许可失败 request=%s: %s", self._name, request_id, ex)

    async def close(self) -> None:
        # 有意语义：只挡新 acquire（直通）；已排队 waiter 不被动唤醒，各自空转到超时
        self._closed = True


class RedisPermit(Permit):
    """Redis 限流器许可：release 归还许可（幂等）并清理持有登记"""

    __slots__ = ("_limiter", "_request_id", "_released")

    def __init__(self, limiter: RedisFairRateLimiter, request_id: str):
        self._limiter = limiter
        self._request_id = request_id
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            await self._limiter._release(self._request_id)
        except Exception:  # noqa: BLE001 —— 归还失败仅告警，不二次抛（lease 兜底回收）
            logger.warning("[%s] 归还许可失败 request=%s", self._limiter._name,
                           self._request_id, exc_info=True)


def _load_lua() -> str:
    """加载 Lua 判头脚本（对齐 Java loadLuaScript）"""
    return _LUA_PATH.read_text(encoding="utf-8")


def _load_release_lua() -> str:
    """加载 Lua 条件释放脚本（lua/permit_release_atomic.lua）"""
    return _RELEASE_LUA_PATH.read_text(encoding="utf-8")