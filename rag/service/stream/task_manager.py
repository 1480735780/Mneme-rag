# -*- coding: utf-8 -*-
"""
rag.service.stream.task_manager - 流式任务管理器（对应 Java StreamTaskManager）

流式任务的注册 / 取消 / 清理，对齐 Java StreamTaskManager 语义：
    - register：本地注册 sender + onCancelSupplier + **属主登记**（owner 进 Redis 而非只留本地：
      停止请求可能落在没跑这条流的节点上）；属主先于收尾回调落地；**Redis 取消标记检测**——
      若标记已设（先取消后注册），按发起方复核通过后立即执行取消补偿（对齐 Java isTaskCancelledInRedis）；
    - bind_task：绑定协程句柄（asyncio.Task，等价 Java bindHandle 绑 StreamCancellationHandle）；
      已取消则立即 task.cancel()；取消时 task.cancel() 使引擎协程中断；
    - is_cancelled：本地取消标志；
    - cancel：**系统侧回收**（SSE 超时 / 客户端断连等，发起方 = __system__ 与任何用户 ID 不撞）；
    - cancel_by_user：**用户主动停止**——taskId 是雪花 ID 时间有序可预测不是访问凭证，必须比对属主：
      Redis owner 与发起方不符 → ClientException（不区分「不存在」与「非属主」，免得停止接口变成
      他人任务的探测器）；随后 publishCancel；
    - publish_cancel：先设 Redis 取消标记（值为发起方，TTL 30min），再广播 `taskId|requester`
      （R-B：载荷带发起方，执行端要靠它复核——只有发布端校验挡不住属主落地前的抢跑）；
    - cancel_local：**执行端复核发起方**（越权取消连 cancelled 都不置——置了会让 register 的
      复核短路，等于把标记复核那道门绕开）→ CAS 防重 → task.cancel() → send CANCEL+DONE + complete；
    - unregister：本地移除 + Redis 取消标记 / 属主键删除。

同步/异步边界：
    - register / bind_task / is_cancelled / unregister 为同步（cache 经 AsyncCacheBridge 桥接，
      对齐 Java 请求线程内阻塞语义）；
    - cancel / cancel_by_user / start / stop 为 async（stop 端点异步调用）；
    - start/stop：订阅生命周期（factory lifespan 调用，对齐 Java @PostConstruct/@PreDestroy 的
      RTopic.addListener/removeListener）；未 start（测试直建）时无订阅回调，cancel 仍本地生效。
      订阅回调解析 `taskId|requester`；无分隔符的裸 taskId 视为 __system__（滚动升级期老节点兼容）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.handler.StreamTaskManager
    - com.nageoffer.ai.ragent.framework.web.StreamTaskManager（R-B 属主复核 + R-A 跨节点广播参考：
      CANCEL_TOPIC / OWNER_KEY_PREFIX / SYSTEM_REQUESTER / publishCancel / cancelByUser / cancelLocal 复核）
    - com.nageoffer.ai.ragent.framework.web.SseEmitterSender（sendCancelAndDone 帧来源）
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from common.exception.business import ClientException
from common.web.sse import encode_event
from rag.service.stream.protocol import CompletionPayload, SSEEventType
from storage.cache import CacheManager, MemoryCacheManager
from storage.cache.bridge import AsyncCacheBridge

logger = logging.getLogger(__name__)

# Redis 取消标记 / 属主 / 广播频道常量（对齐 Java StreamTaskManager 常量）
CANCEL_TOPIC = "ragent:stream:cancel"
CANCEL_KEY_PREFIX = "ragent:stream:cancel:"
OWNER_KEY_PREFIX = "ragent:stream:owner:"
CANCEL_TTL_SECONDS = 30 * 60  # 30min（对齐 Java Duration.ofMinutes(30)）

# 系统侧回收的发起方占位，与任何用户 ID 都不会撞（用户 ID 是雪花数字串）
SYSTEM_REQUESTER = "__system__"

# 广播载荷 taskId|requester 的分隔符：taskId 与用户 ID 都是雪花数字串，不含竖线
PAYLOAD_SEPARATOR = "|"


@dataclass
class _StreamTaskInfo:
    """单任务运行时信息（对应 Java 内部 class StreamTaskInfo）"""

    cancelled: bool = False
    owner_user_id: Optional[str] = None
    sender: Optional[Any] = None  # SseQueue 或记录型发送器
    on_cancel_supplier: Optional[Callable[[], CompletionPayload]] = None
    task: Optional[Any] = None  # asyncio.Task（Python 侧协程句柄，等价 Java StreamCancellationHandle）


class StreamTaskManager:
    """流式任务管理器（本地注册表 + Redis 取消标记/属主 + Pub/Sub 广播）"""

    def __init__(
        self,
        cache: Optional[CacheManager] = None,
        enabled_cross_node: bool = True,
    ):
        self._cache: CacheManager = cache or MemoryCacheManager()
        self._enabled_cross_node = enabled_cross_node
        self._tasks: Dict[str, _StreamTaskInfo] = {}
        self._lock = threading.RLock()
        self._subscription: Optional[Any] = None  # 订阅句柄（start() 后持有）

    # ==================== 订阅生命周期（对齐 Java @PostConstruct/@PreDestroy） ====================

    async def start(self) -> None:
        """订阅跨节点取消频道：任一节点 publishCancel 后，本节点经回调复核并执行 cancelLocal。

        未调用 start（单测直建 / 广播后端不支持）时无回调——cancel/cancel_by_user 的
        本地直发兜底保证本节点语义不变。重复 start 幂等（先停旧订阅）。
        """
        if not self._enabled_cross_node:
            return
        await self.stop()
        self._subscription = await self._cache.subscribe(CANCEL_TOPIC, self._on_cancel_broadcast)
        if self._subscription is None:
            logger.info("流式取消广播不可用（后端不支持订阅），跨节点取消退化为标记兜底")

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.close()
            self._subscription = None

    def _on_cancel_broadcast(self, payload: str) -> None:
        """订阅回调（对齐 Java RTopic 监听器 → cancelLocal）"""
        if not payload:
            return
        separator = payload.find(PAYLOAD_SEPARATOR)
        # 滚动升级期老节点仍广播裸 taskId，它在发布前已做过同样的属主校验，按系统侧收
        if separator < 0:
            task_id, requester = payload, SYSTEM_REQUESTER
        else:
            task_id, requester = payload[:separator], payload[separator + 1:]
        self.cancel_local(task_id, requester)

    # ==================== 注册 / 绑定 / 查询 ====================

    def register(
        self,
        task_id: str,
        sender: Any,
        on_cancel_supplier: Callable[[], CompletionPayload],
        owner_user_id: Optional[str] = None,
    ) -> None:
        """
        注册流式任务（对齐 Java register）

        绑 sender + onCancelSupplier + 属主；属主先于收尾回调落地，且**进 Redis 而非只留本地**
        （停止请求可能落在没跑这条流的节点上）；若 Redis 已标记取消（先取消后注册竞态）→
        按发起方复核通过后立即取消补偿（sendCancelAndDone + complete）。
        """
        info = self._get_or_create(task_id)
        info.owner_user_id = owner_user_id
        info.sender = sender
        info.on_cancel_supplier = on_cancel_supplier
        if self._enabled_cross_node and owner_user_id:
            try:
                AsyncCacheBridge.run(self._cache.set(
                    self._owner_key(task_id), owner_user_id, ttl=CANCEL_TTL_SECONDS
                ))
            except Exception as ex:  # noqa: BLE001 —— 属主写失败不阻断注册（本地 info 仍有属主）
                logger.warning("写入流式任务属主失败，taskId=%s: %s", task_id, ex)
        if self._is_cancelled_in_redis(info, task_id):
            payload = info.on_cancel_supplier()
            self._send_cancel_and_done(sender, payload)
            sender.close()

    def bind_task(self, task_id: str, task: Any) -> None:
        """
        绑定协程句柄（等价 Java bindHandle 绑 StreamCancellationHandle）

        已取消则立即 task.cancel()（任务启动后取消标记早已在——此处兜底启动即取消的场景）。
        """
        info = self._get_or_create(task_id)
        info.task = task
        if info.cancelled and task is not None:
            task.cancel()

    def is_cancelled(self, task_id: str) -> bool:
        """本地取消标志（对齐 Java isCancelled）；未注册/未取消返回 False"""
        with self._lock:
            info = self._tasks.get(task_id)
            return info is not None and info.cancelled

    # ==================== 取消 ====================

    async def cancel(self, task_id: str) -> None:
        """
        系统侧取消（对齐 Java cancel：SSE 超时 / 客户端断连等回收，容器回调上没有登录用户）：
        发起方 = __system__，标记 + 广播 + 本地收尾。
        """
        await self._publish_cancel(task_id, SYSTEM_REQUESTER)

    async def cancel_by_user(self, task_id: str, requester: str) -> None:
        """
        用户主动停止（对齐 Java cancelByUser）：taskId 时间有序可预测，不是访问凭证，必须比对属主

        属主查不到多半是任务已结束，也可能是注册还没落地——不区分「不存在」与「非属主」，
        交给标记 + 广播的执行端复核兜底。
        """
        if self._enabled_cross_node:
            try:
                owner = await self._cache.get(self._owner_key(task_id))
            except Exception as ex:  # noqa: BLE001 —— 属主读取失败按「无属主」放行，交执行端复核
                logger.warning("读取流式任务属主失败，taskId=%s: %s", task_id, ex)
                owner = None
            if owner and requester and owner != requester:
                logger.warning(
                    "拒绝越权停止流式任务，taskId：%s，属主：%s，发起方：%s", task_id, owner, requester
                )
                raise ClientException("任务不存在或已结束")
        await self._publish_cancel(task_id, requester or SYSTEM_REQUESTER)

    async def _publish_cancel(self, task_id: str, requester: str) -> None:
        """对齐 Java publishCancel：先设 Redis 取消标记（值为发起方），再广播，最后本地收尾"""
        if self._enabled_cross_node:
            try:
                await self._cache.set(self._cancel_key(task_id), requester, ttl=CANCEL_TTL_SECONDS)
            except Exception as ex:  # noqa: BLE001 —— 标记写入失败不阻断本地取消
                logger.warning("设置 Redis 取消标记失败，taskId=%s: %s", task_id, ex)
            try:
                published = await self._cache.publish(CANCEL_TOPIC, f"{task_id}{PAYLOAD_SEPARATOR}{requester}")
            except Exception as ex:  # noqa: BLE001 —— 广播失败不阻断本地取消
                published = False
                logger.warning("发布取消广播失败，taskId=%s: %s", task_id, ex)
            if not published:
                logger.debug("取消广播未投递（后端不支持或异常），本地收尾兜底，taskId=%s", task_id)
        self.cancel_local(task_id, requester)

    def cancel_local(self, task_id: str, requester: str = SYSTEM_REQUESTER) -> None:
        """
        本地取消（对齐 Java cancelLocal）：执行端复核发起方 → CAS 防重 → task.cancel() → 收尾

        复核不通过时连 cancelled 都不置——置了会让 register 的标记复核短路，
        等于把标记复核那道门绕开。
        """
        with self._lock:
            info = self._tasks.get(task_id)
            if info is None:
                return

            if not self._is_requester_allowed(info, requester):
                logger.warning(
                    "拒绝越权取消流式任务，taskId：%s，属主：%s，发起方：%s",
                    task_id, info.owner_user_id, requester,
                )
                return

            if info.cancelled:
                return  # CAS 防重：已取消不再执行
            info.cancelled = True

        if info.task is not None:
            info.task.cancel()

        if info.sender is not None and info.on_cancel_supplier is not None:
            payload = info.on_cancel_supplier()
            self._send_cancel_and_done(info.sender, payload)
            info.sender.close()

    # ==================== 清理 ====================

    def unregister(self, task_id: str) -> None:
        """清理解除：本地注册移除 + Redis 取消标记 / 属主键删除（对齐 Java unregister）"""
        with self._lock:
            self._tasks.pop(task_id, None)
        if self._enabled_cross_node:
            try:
                AsyncCacheBridge.run(self._cache.delete(self._cancel_key(task_id)))
                AsyncCacheBridge.run(self._cache.delete(self._owner_key(task_id)))
            except Exception as ex:  # noqa: BLE001
                logger.warning("删除 Redis 取消标记/属主失败，taskId=%s: %s", task_id, ex)

    # ==================== 内部 ====================

    def _get_or_create(self, task_id: str) -> _StreamTaskInfo:
        with self._lock:
            if task_id not in self._tasks:
                self._tasks[task_id] = _StreamTaskInfo()
            return self._tasks[task_id]

    def _is_cancelled_in_redis(self, info: _StreamTaskInfo, task_id: str) -> bool:
        """
        Redis 是否已标记取消（对齐 Java isTaskCancelledInRedis）；命中且发起方合法则同步本地状态

        标记可能先于注册到达，那一刻还没有属主可比对，只能推到这里复核。
        """
        if info.cancelled:
            return True
        if not self._enabled_cross_node:
            return False
        try:
            marker = AsyncCacheBridge.run(self._cache.get(self._cancel_key(task_id)))
        except Exception:  # noqa: BLE001 —— 查 Redis 失败视为未取消，不阻断注册
            return False
        if marker is None:
            return False
        # 兼容旧形态布尔标记（滚动升级期老节点写入）按系统侧收
        requester = marker if isinstance(marker, str) else SYSTEM_REQUESTER
        if not self._is_requester_allowed(info, requester):
            logger.warning(
                "忽略非属主埋下的取消标记，taskId：%s，属主：%s，发起方：%s",
                task_id, info.owner_user_id, requester,
            )
            return False
        info.cancelled = True
        return True

    @staticmethod
    def _is_requester_allowed(info: _StreamTaskInfo, requester: str) -> bool:
        """
        系统侧回收无条件放行；用户侧只认精确属主，属主还没落地（注册未发生）时一律不认
        （对齐 Java isRequesterAllowed）
        """
        if requester == SYSTEM_REQUESTER:
            return True
        return bool(info.owner_user_id) and info.owner_user_id == requester

    def _cancel_key(self, task_id: str) -> str:
        return f"{CANCEL_KEY_PREFIX}{task_id}"

    def _owner_key(self, task_id: str) -> str:
        return f"{OWNER_KEY_PREFIX}{task_id}"

    def _send_cancel_and_done(self, sender: Any, payload: Optional[CompletionPayload]) -> None:
        """发送 CANCEL + DONE 帧（对齐 Java sendCancelAndDone）；payload 为空回落默认 CompletionPayload"""
        actual = payload if payload is not None else CompletionPayload(message_id=None, title=None)
        sender.push(encode_event(SSEEventType.CANCEL.value, actual.to_json()))
        sender.push(encode_event(SSEEventType.DONE.value, "[DONE]"))
