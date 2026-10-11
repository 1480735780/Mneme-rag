# -*- coding: utf-8 -*-
"""
core.llm.observation.emit - 编排层观测发射器（P10.1 T1.3，D1/D4/D5 发射点纪律）

RoutingLLMService.chat() 的观测埋点协助器，三个发射点纪律的落地形态：
    - 发射点物化（D5）：observer 在请求 task 内构造，current_scope() 此刻读取——
      flush 消费端（另一 task）读 contextvar 只会读到空气；
    - usage 属性只在发射点消费（D1）：getattr(result, "usage", None) 拿不到
      （裸 str，下游字符串变换产物）即回落估算，绝不依赖属性活过编排层；
    - 一行 = 一次真实 API 调用（D4）：caller 包装器逐次计时记录，transient 重试
      天然逐行覆盖（每次重试都是计费调用）。

is_final 语义 = 本行是该逻辑调用的最后一次真实调用（无论成败）。append-only 明细
不可回填，故 finish() 在请求终态统一补标 + 投递（幂等；观测未装配时全程零开销）。
canceled 不在本发射器产生（用户断开属流式链路，T1.4 落地）。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, List, Optional
from uuid import uuid4

from core.llm.observation.events import (
    CALL_KIND_CHAT,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TOKEN_SOURCE_ESTIMATED,
    TOKEN_SOURCE_MIXED,
    TOKEN_SOURCE_REAL,
    CallEvent,
)
from core.llm.observation.scope import current_scope
from core.llm.result import ChatResult, UsageStats
from core.llm.token import HeuristicTokenCounterService

# 发射端口（ObservationHub.emit 的鸭子类型：非阻塞 put_nowait；None = 观测未装配）
EventSink = Callable[[CallEvent], bool]
# 模型调用器（chat.py 传入的 client.chat 包装，签名对齐 ModelCaller）
ModelCaller = Callable[[Any, Any], Awaitable[Any]]

_COUNTER = HeuristicTokenCounterService()


def _estimate_text_tokens(text: Optional[str]) -> int:
    """启发式估算（D6：不引 tiktoken；异常检测看倍数级变化，±10% 误差无感）"""
    if not text:
        return 0
    count = _COUNTER.count_tokens(text)
    return count if count is not None else 0


def resolve_error_type(error: BaseException) -> str:
    """错误分类（Q11）：ModelClientException 取其 error_type 枚举值，未知异常取类名"""
    error_type = getattr(error, "error_type", None)
    if error_type is not None:
        return getattr(error_type, "value", str(error_type))
    return type(error).__name__


@dataclass(frozen=True)
class _TokenUsage:
    """token 用量与来源的解析结果（D6：real/estimated/mixed 三值口径）"""

    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    token_source: Optional[str]


def _resolve_token_usage(
    usage: Optional[UsageStats], prompt_estimate: int, completion_text: Optional[str]
) -> _TokenUsage:
    """真实优先、估算兜底：两段各自独立判定，来源取二者的交集口径（real/mixed/estimated）。

    completion 只有在「存在输出概念」时才参与来源投票（真实用量或估算 > 0）——
    embedding/rerank 无输出概念，否则仅凭 completion 票会把 prompt 真实值
    污染成 mixed（T1.5）。
    """
    prompt_real = usage.prompt_tokens if usage else None
    completion_real = usage.completion_tokens if usage else None
    prompt_tokens = prompt_real if prompt_real is not None else (prompt_estimate or None)
    completion_est = _estimate_text_tokens(completion_text)
    completion_tokens = completion_real if completion_real is not None else (completion_est or None)
    sources = {
        TOKEN_SOURCE_REAL if prompt_real is not None else TOKEN_SOURCE_ESTIMATED,
    }
    if completion_real is not None or completion_est > 0:
        sources.add(TOKEN_SOURCE_REAL if completion_real is not None else TOKEN_SOURCE_ESTIMATED)
    token_source = sources.pop() if len(sources) == 1 else TOKEN_SOURCE_MIXED
    return _TokenUsage(prompt_tokens, completion_tokens, token_source)


@contextmanager
def observed_call(
    sink: Optional[EventSink],
    *,
    call_kind: str,
    prompt_texts: Iterable[Optional[str]] = (),
    tier: Optional[str] = None,
    estimate_completion: bool = False,
    real_result_types: Optional[tuple] = None,
):
    """服务层发射点统一包装（T1.6）：yield caller 包装器，退出 finish。

    用法（embedding/rerank/vlm 服务层，与 chat 的编排层埋点同一套纪律）：
        with observed_call(sink, call_kind=CALL_KIND_EMBEDDING, prompt_texts=texts) as wrap:
            return await self._executor.execute_with_fallback(..., caller=wrap(raw_caller))
    """
    if sink is None:
        yield lambda caller: caller
        return
    observer = CallObserver(
        sink, call_kind=call_kind, prompt_texts=prompt_texts, tier=tier,
        estimate_completion=estimate_completion, real_result_types=real_result_types,
    )
    try:
        yield observer.wrap_caller
    finally:
        observer.finish()


class CallObserver:
    """
    一次非流式 LLM 逻辑调用的观测记录器（chat/embedding/rerank/vlm 共用，T1.6 泛化）

    用法：
        observer = CallObserver.for_chat(sink, request=request, tier=tier)
        result = await executor.execute_with_fallback(..., caller=observer.wrap_caller(raw_caller))
        finally: observer.finish()

    Args:
        sink:    事件投递端口（ObservationHub.emit）；None = 观测未装配，记录但不投递。
        call_kind: 事件 call_kind（chat/embedding/rerank/vlm）。
        prompt_texts: prompt 估算来源文本列表（chat=消息内容；embedding=输入文本；
            rerank=query+候选文本；vlm=图生文提示词）。
        tier:    请求档位（Tier.value；None = 走默认档路由）。
        estimate_completion: 是否存在输出概念（chat/vlm True；embedding/rerank False——
            无输出概念时不投 completion 来源票，token_source 不被污染成 mixed）。
        real_result_types: 真实 API 调用的返回类型白名单；None = 不判定。设置后
            （rerank 场景 = (RerankResult,)），白名单外的成功结果为短路直通
            （如候选数不足未调 API），不落行（D3）。
    """

    def __init__(
        self,
        sink: Optional[EventSink],
        *,
        call_kind: str = CALL_KIND_CHAT,
        prompt_texts: Iterable[Optional[str]] = (),
        tier: Optional[str] = None,
        estimate_completion: bool = True,
        real_result_types: Optional[tuple] = None,
    ) -> None:
        self._sink = sink
        self._kind = call_kind
        self._tier = tier
        self._estimate_completion = estimate_completion
        self._real_result_types = real_result_types
        self._scope = current_scope()  # D5：发射点物化（此刻仍在请求 task 的 scope 内）
        self.request_uid = uuid4().hex  # 逻辑调用分组键（D4，VARCHAR(36) 内）
        self._prompt_estimate = sum(_estimate_text_tokens(text) for text in prompt_texts)
        self._events: List[CallEvent] = []
        self._finished = False

    @classmethod
    def for_chat(
        cls, sink: Optional[EventSink], *, request: Any, tier: Optional[str] = None
    ) -> "CallObserver":
        """chat 便捷构造：prompt 估算来源 = 请求消息内容列表"""
        return cls(
            sink,
            call_kind=CALL_KIND_CHAT,
            prompt_texts=[
                getattr(message, "content", None)
                for message in (getattr(request, "messages", None) or ())
            ],
            tier=tier,
            estimate_completion=True,
        )

    # ==================== caller 包装（每次真实调用一行） ====================

    def wrap_caller(self, caller: ModelCaller) -> ModelCaller:
        """包装模型调用器：逐次计时记录；异常分类后原样上抛（不吞不转译，
        executor 的熔断反馈/降级/transient 重试语义零改动）"""

        async def observed(client: Any, target: Any) -> Any:
            attempt_no = len(self._events) + 1
            start = time.monotonic()
            try:
                result = await caller(client, target)
            except Exception as e:
                latency_ms = int((time.monotonic() - start) * 1000)
                self._events.append(
                    self._build_event(
                        target, attempt_no, latency_ms,
                        status=STATUS_FAILED, error=resolve_error_type(e),
                        usage=None, completion_text=None,
                    )
                )
                raise
            latency_ms = int((time.monotonic() - start) * 1000)
            # D3：real_result_types 白名单外的成功结果 = 短路直通（如 rerank 候选不足
            # 未调 API），不是真实计费调用，不落行
            if (
                self._real_result_types is not None
                and not isinstance(result, self._real_result_types)
            ):
                return result
            # D1 纪律：usage 只在此消费；裸 str（无属性）按缺失处理回落估算
            usage = getattr(result, "usage", None)
            usage = usage if isinstance(usage, UsageStats) and not usage.is_empty else None
            completion_text = (
                str(result) if result is not None and self._estimate_completion else None
            )
            self._events.append(
                self._build_event(
                    target, attempt_no, latency_ms,
                    status=STATUS_SUCCESS, error=None,
                    usage=usage, completion_text=completion_text,
                )
            )
            return result

        return observed

    # ==================== 终态（is_final 补标 + 统一投递） ====================

    def finish(self) -> None:
        """请求终态：is_final 落最后一行并统一投递（幂等；观测未装配时零开销）"""
        if self._finished:
            return
        self._finished = True
        if self._sink is None or not self._events:
            return
        self._events[-1] = replace(self._events[-1], is_final=True)
        for event in self._events:
            self._sink(event)  # put_nowait：非阻塞，满即丢由队列计数（D2）

    # ==================== 内部 ====================

    def _build_event(
        self,
        target: Any,
        attempt_no: int,
        latency_ms: int,
        *,
        status: str,
        error: Optional[str],
        usage: Optional[UsageStats],
        completion_text: Optional[str],
    ) -> CallEvent:
        # 失败行不记 token：响应未到达，服务端实际消耗不可知，估算只会制造假数据
        # （Q11/D4：canceled 之外的失败行 token 恒为 None）
        tokens = (
            _resolve_token_usage(usage, self._prompt_estimate, completion_text)
            if status == STATUS_SUCCESS
            else _TokenUsage(None, None, None)
        )
        return CallEvent(
            request_uid=self.request_uid,
            call_kind=self._kind,
            provider=getattr(target.candidate, "provider", "") or "",
            model=getattr(target.candidate, "model", "") or "",
            status=status,
            scene=self._scope.scene,
            purpose=self._scope.purpose,
            trace_id=self._scope.trace_id,
            user_id=self._scope.user_id,
            tier=self._tier,
            attempt_no=attempt_no,
            is_final=False,  # finish() 统一补标终态行
            error_type=error,
            prompt_tokens=tokens.prompt_tokens,
            completion_tokens=tokens.completion_tokens,
            token_source=tokens.token_source,
            latency_ms=latency_ms,
            create_time=datetime.now().isoformat(),
        )


class StreamCallObserver:
    """
    一次流式 chat 逻辑调用的观测记录器（RoutingLLMService.stream_chat 专用，T1.4）

    与 ChatCallObserver 同构：缓冲行 + finish() 终态统一补标 is_final 投递——
    stream_chat 把 finish() 放在 finally，**canceled 路径也保证落行**（Q11：取消
    不进失败率但已产出内容计成本）。数据源是 ProbeStreamBridge 的遥测
    （ttft_ms / content_text / usage），token 口径 = 真实尾包优先、内容估算兜底。

    四条出口路径的落行语义（D4/Q11 + 执行期决策 B）：
        - 首包前失败（探活 ERROR/NO_CONTENT）→ failed 行，tokens=None（无内容无消耗）
        - TTFT 超预算切换 → failed 行，error_type="timeout"（决策 B 新字面量）
        - 上游取消 → canceled 行（部分输出照常计成本）
        - 流终态（complete / 首包后出错）→ success / failed 行
    探活 NO_CONTENT 归 invalid_response（响应形态问题）；探活 ERROR 沿用异常自身
    error_type。
    """

    def __init__(
        self,
        sink: Optional[EventSink],
        *,
        request: Any,
        tier: Optional[str] = None,
    ) -> None:
        self._sink = sink
        self._scope = current_scope()  # D5：发射点物化（构造发生在请求 task 内）
        self.request_uid = uuid4().hex
        self._tier = tier  # stream_chat 无 tier 参数，恒 None（选档在 selector 内部）
        self._prompt_estimate = sum(
            _estimate_text_tokens(getattr(message, "content", None))
            for message in (getattr(request, "messages", None) or ())
        )
        self._events: List[CallEvent] = []
        self._finished = False

    # ==================== 探活失败路径（无内容无 token） ====================

    def record_probe_failure(
        self, target: Any, *, latency_ms: int, error_type: str
    ) -> None:
        """首包前失败（探活 ERROR / NO_CONTENT / TTFT 超时）：tokens 恒 None"""
        self._events.append(
            self._build_event(
                target, len(self._events) + 1, latency_ms,
                status=STATUS_FAILED, error=error_type,
                tokens=_TokenUsage(None, None, None), ttft_ms=None,
            )
        )

    # ==================== 终态路径（数据源 = bridge 遥测） ====================

    def record_terminal(
        self,
        target: Any,
        bridge: Any,
        *,
        status: str,
        error: Optional[BaseException],
        started_at: float,
    ) -> None:
        """流终态行（成功 / 首包后出错 / 上游取消）：部分输出照常计成本（Q11）"""
        latency_ms = int((time.monotonic() - started_at) * 1000)
        usage = getattr(bridge, "usage", None)
        usage = usage if isinstance(usage, UsageStats) and not usage.is_empty else None
        content_text = getattr(bridge, "content_text", None) or ""
        tokens = _resolve_token_usage(usage, self._prompt_estimate, content_text)
        self._events.append(
            self._build_event(
                target, len(self._events) + 1, latency_ms,
                status=status,
                error=resolve_error_type(error) if error is not None else None,
                tokens=tokens,
                ttft_ms=getattr(bridge, "ttft_ms", None),
            )
        )

    # ==================== 终态收尾（与非流式同构） ====================

    def finish(self) -> None:
        """请求终态：is_final 落最后一行并统一投递（幂等；观测未装配时零开销）"""
        if self._finished:
            return
        self._finished = True
        if self._sink is None or not self._events:
            return
        self._events[-1] = replace(self._events[-1], is_final=True)
        for event in self._events:
            self._sink(event)  # put_nowait：非阻塞，满即丢由队列计数（D2）

    # ==================== 内部 ====================

    def _build_event(
        self,
        target: Any,
        attempt_no: int,
        latency_ms: int,
        *,
        status: str,
        error: Optional[str],
        tokens: _TokenUsage,
        ttft_ms: Optional[int],
    ) -> CallEvent:
        return CallEvent(
            request_uid=self.request_uid,
            call_kind=CALL_KIND_CHAT,
            provider=getattr(target.candidate, "provider", "") or "",
            model=getattr(target.candidate, "model", "") or "",
            status=status,
            scene=self._scope.scene,
            purpose=self._scope.purpose,
            trace_id=self._scope.trace_id,
            user_id=self._scope.user_id,
            tier=self._tier,
            attempt_no=attempt_no,
            is_final=False,  # finish() 统一补标终态行
            error_type=error,
            prompt_tokens=tokens.prompt_tokens,
            completion_tokens=tokens.completion_tokens,
            token_source=tokens.token_source,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            create_time=datetime.now().isoformat(),
        )
