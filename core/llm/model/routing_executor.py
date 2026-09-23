# -*- coding: utf-8 -*-
"""
core.llm.model.routing_executor - 模型路由执行器（对应 ragent 的 ModelRoutingExecutor）

本模块负责在多个模型候选目标之间执行调度与故障转移（Fallback）：
按 ModelSelector 产出的有序候选列表逐个尝试调用，第一个成功即返回；
失败则记录到 ModelHealthStore（驱动断路器状态机），并切换下一个候选。

架构对应关系：
    Ragent (Java)                                Mneme-rag (Python)
    ──────────────────────────────────────────────────────────────
    infra/model/ModelRoutingExecutor.java  --> core/llm/model/routing_executor.py
    infra/model/ModelCaller.java           --> 本模块的 ModelCaller 类型别名
    infra/model/ModelHealthStore.java      --> core/llm/model/health_store.py
    infra/enums/ModelCapability            --> capability 字符串参数（MVP 暂不建枚举）

核心流程（与 Java executeWithFallback 逐行对齐）：
    空候选                  → 抛 RoutingExecutionError（对应 RemoteException）
    遍历 targets：
        client_resolver     → 解析客户端，缺失则 warn 跳过
        health_store.allow_call → 熔断中（返回 None）则跳过（执行期双保险）
        caller(client, target) → 成功：mark_success 并返回结果
                                失败：mark_failure，记录最后错误，继续下一候选
    全部失败                → 抛 RoutingExecutionError（携带最后失败原因）

设计说明：
    - 与 Java 同步调用不同，Python 的 BaseChatClient.chat 是异步方法，
      因此 caller 与 execute_with_fallback 均为 async；调度过程无锁内 IO；
    - release_half_open_permit 在 finally 中调用（对齐 health_store 的用法契约），
      该调用幂等：mark_success/mark_failure 已复位 half_open_in_flight 时不会误释放；
    - 本类不感知具体模型供应商，只承担"遍历 + 健康检查 + 结果反馈"的调度职责。
"""

import logging
from typing import Awaitable, Callable, List, Optional, TypeVar, Union

from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from ..enums import ModelCapability
from .health_store import ModelHealthStore
from .model_target import ModelTarget

logger = logging.getLogger(__name__)

# 泛型参数：C = 客户端类型，T = 调用返回类型（对应 Java 的 <C, T>）
C = TypeVar("C")
T = TypeVar("T")

# 客户端解析器：ModelTarget → 客户端实例，缺失返回 None（对应 Java 的 Function<ModelTarget, C>）
ClientResolver = Callable[[ModelTarget], Optional[C]]
# 模型调用器：异步执行一次模型调用（对应 Java ModelCaller 接口的 call(client, target)）
ModelCaller = Callable[[C, ModelTarget], Awaitable[T]]


class RoutingExecutionError(Exception):
    """
    模型路由执行失败（对应 Java 的 RemoteException）。

    两种触发场景：
        1. 候选列表为空：message = "No <capability> model candidates available"
        2. 所有候选均失败：message = "All <capability> model candidates failed: <last>"
           此时 cause 保存最后一个失败异常（对应 RemoteException 的 cause）。
    """

    def __init__(self, message: str, cause: Optional[BaseException] = None) -> None:
        super().__init__(message)
        self.cause = cause


class RoutingExecutor:
    """
    模型路由执行器（对应 Java 的 ModelRoutingExecutor）。

    在多个模型候选之间执行故障转移调度，配合 ModelHealthStore 实现熔断降级：
    熔断中的模型在执行期被 allow_call 拦截，不会发起真实调用
    （选择期已被 selector 前置过滤，此处是双保险）。

    使用示例：
        executor = RoutingExecutor(health_store)
        result = await executor.execute_with_fallback(
            capability="Chat",
            targets=targets,
            client_resolver=lambda t: clients_by_provider.get(t.candidate.provider),
            caller=lambda client, target: client.chat(request, target),
        )
    """

    def __init__(
        self,
        health_store: ModelHealthStore,
        max_fallback: Optional[int] = None,
        transient_retries: int = 0,
    ) -> None:
        """注入健康状态存储与降级策略（对应 Java 的 @RequiredArgsConstructor 构造注入）。

        Args:
            health_store: 模型健康状态存储。
            max_fallback: 单次调用最大降级次数（doc 11.4）；None=不限（现状）。
                N 表示最多允许 N 次"真失败"（= N+1 个真尝试的候选），
                被跳过的候选（client 缺失/熔断）不占预算；
                超出后截断，RoutingExecutionError 消息注明 max_fallback。
            transient_retries: 临时性故障（ModelClientException.NETWORK_ERROR）
                的重试次数（doc 11.5）；0=不重试（现状）。重试期间不记
                mark_failure、不发降级日志（健康反馈以候选为粒度）；
                RATE_LIMITED（429）显式不重试——重试会加剧限流。
        """
        self.health_store = health_store
        self.max_fallback = max_fallback
        self.transient_retries = max(0, transient_retries)

    async def execute_with_fallback(
        self,
        capability: Union[str, ModelCapability],
        targets: List[ModelTarget],
        client_resolver: ClientResolver,
        caller: ModelCaller,
    ) -> T:
        """
        带故障转移的模型调用执行。

        Args:
            capability: 能力显示名或 ModelCapability 枚举（"Chat" / "Embedding" /
                "Rerank"，对应 ModelCapability.displayName），仅用于错误与日志文案。
            targets: 有序候选目标列表（由 ModelSelector 产出，已过滤不健康模型）。
            client_resolver: ModelTarget → 客户端实例；返回 None 表示该供应商客户端缺失。
            caller: 异步模型调用器，执行一次 client 对 target 的调用。

        Returns:
            T: 第一个成功候选的调用结果。

        Raises:
            RoutingExecutionError: 候选列表为空，或所有候选均调用失败。
        """
        capability_name = self._capability_name(capability)
        if not targets:
            raise RoutingExecutionError(f"No {capability_name} model candidates available")

        last_error: Optional[BaseException] = None
        failed_count = 0  # 已发生的真实失败次数（跳过的候选不计入）
        truncated_by_max_fallback = False
        for target_index, target in enumerate(targets):
            if (
                self.max_fallback is not None
                and failed_count > self.max_fallback
            ):
                # 降级深度已用尽（doc 11.4）：剩余候选不尝试，避免延迟过高
                truncated_by_max_fallback = True
                logger.warning(
                    "%s fallback depth reached (max_fallback=%d), %d candidate(s) skipped. "
                    "lastModelId=%s",
                    capability_name, self.max_fallback,
                    len(targets) - target_index, target.id,
                )
                break

            client = client_resolver(target)
            if client is None:
                logger.warning(
                    "%s provider client missing: provider=%s, modelId=%s",
                    capability_name, target.candidate.provider, target.id,
                )
                continue

            permit = self.health_store.allow_call(target.id)
            if permit is None:
                continue  # 熔断中（执行期双保险），跳过该候选

            try:
                response = await self._call_with_transient_retry(
                    caller, client, target, capability_name
                )
            except Exception as e:
                last_error = e
                self.health_store.mark_failure(target.id)
                # 结构化降级日志（doc 11.3）：extra 字段供日志后端按
                # failed_provider / error_type 聚合降级频率与根因；
                # fallback_count = 本次失败前已发生的降级次数（首个失败候选为 0）
                logger.warning(
                    "%s model failed, fallback to next. modelId=%s, provider=%s",
                    capability_name, target.id, target.candidate.provider,
                    exc_info=e,
                    extra={
                        "capability": capability_name,
                        "failed_model_id": target.id,
                        "failed_provider": target.candidate.provider,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "fallback_count": failed_count,
                    },
                )
                failed_count += 1
                continue
            else:
                self.health_store.mark_success(target.id)
                return response
            finally:
                # 幂等防御：mark_success/mark_failure 已复位时不会误释放
                self.health_store.release_half_open_permit(permit)

        if truncated_by_max_fallback:
            raise RoutingExecutionError(
                f"All attempted {capability_name} model candidates failed "
                f"(stopped by max_fallback={self.max_fallback}): "
                f"{last_error if last_error is not None else 'unknown'}",
                cause=last_error,
            )
        raise RoutingExecutionError(
            f"All {capability_name} model candidates failed: "
            f"{last_error if last_error is not None else 'unknown'}",
            cause=last_error,
        )

    # ==================== 临时性故障重试（doc 11.5） ====================

    async def _call_with_transient_retry(
        self,
        caller: ModelCaller,
        client,
        target: ModelTarget,
        capability_name: str,
    ):
        """带临时性故障重试的单候选调用；耗尽后抛最后一次异常（交由降级路径）。

        重试仅针对 NETWORK_ERROR（网络抖动幂等，doc 11.5 建议）；
        RATE_LIMITED / 其他 ModelClientException / 未知异常直接抛出。
        重试期间不 mark_failure / 不发降级日志——健康反馈以候选为粒度，
        重试成功则该候选零失败记录。
        """
        attempt = 0
        while True:
            try:
                return await caller(client, target)
            except Exception as e:
                if attempt >= self.transient_retries or not self._is_transient(e):
                    raise
                attempt += 1
                # 重试可观测（对齐 11.3 结构化风格）：retry_attempt 从 1 起
                logger.warning(
                    "%s transient error, retrying. modelId=%s, provider=%s",
                    capability_name, target.id, target.candidate.provider,
                    exc_info=e,
                    extra={
                        "capability": capability_name,
                        "retry_model_id": target.id,
                        "retry_provider": target.candidate.provider,
                        "error_type": type(e).__name__,
                        "retry_attempt": attempt,
                    },
                )

    @staticmethod
    def _is_transient(error: BaseException) -> bool:
        """是否可重试的临时性故障：仅 NETWORK_ERROR。

        RATE_LIMITED（429）有意排除：限流场景重试会加剧限流（doc 11.5 风险）；
        SERVER_ERROR 等其余类型失败原因不可幂等假定，直接降级。
        """
        return (
            isinstance(error, ModelClientException)
            and error.error_type == ModelClientErrorType.NETWORK_ERROR
        )

    @staticmethod
    def _capability_name(capability: Union[str, ModelCapability]) -> str:
        """
        解析能力显示名：ModelCapability 枚举取其 display_name，字符串原样返回。
        向后兼容：既有调用方传 "Chat" 等字符串时行为不变。
        """
        if isinstance(capability, ModelCapability):
            return capability.display_name
        return str(capability)
