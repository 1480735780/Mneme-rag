# -*- coding: utf-8 -*-
"""
rag.service.stream.trace_runner - 流式对话追踪上下文与入口包装（对应 Java RagTraceContext +
StreamChatTraceRunner + ForwardingStreamCallback）

三层：
    - RagTraceContext：contextvars 版 trace_id / task_id / 节点栈（对齐 Java TransmittableThreadLocal +
      RagTraceContext）。节点栈用**不可变 tuple** 存储——contextvars 子任务拷贝的是 tuple 引用快照，
      子任务 push/pop 产生新 tuple，天然实现 Java 注释强调的「子线程深拷贝、父子节点 ID 不串挂」语义。
    - ForwardingStreamCallback：透传式 StreamCallback 装饰器（对齐 Java ForwardingStreamCallback）——
      onContent/onThinking 直接透传 delegate；onComplete/onError 透传后 **CAS-once** 触发 on_finish；
      on_first_content 钩子仅在首个 onContent 触发一次。
    - StreamChatTraceRunner：run() 包装（对齐 Java StreamChatTraceRunner.run）——release 分支直通；
      enabled 分支 startRun（RUNNING）→ 包裹 traceAware callback → 设 context → 执行业务 →
      finally clear；首个 on_content 记 USER_TTFT 节点；on_finish 收尾 finish_run SUCCESS/ERROR。
      错误信息经 max_error_length 截断。

record_service 为鸭子类型接口（宿主由 5.1 M5 RagTraceRecordService 提供），需实现：
    start_run(row) / finish_run(trace_id, status, error_message, end_time, duration_ms)
    start_node(row) / finish_node(trace_id, node_id, status, end_time, duration_ms, error_message=None)

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.trace.RagTraceContext
    - com.nageoffer.ai.ragent.infra.chat.ForwardingStreamCallback
    - com.nageoffer.ai.ragent.rag.trace.StreamChatTraceRunner
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Tuple

from common.util.snowflake import default_generator
from rag.dao.support import now_iso

logger = logging.getLogger(__name__)

# ==================== 常量（对齐 Java StreamChatTraceRunner） ====================

ENTRY_METHOD = "RAGChatService#streamChat"
TRACE_NAME = "rag-stream-chat"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_ERROR = "ERROR"
USER_TTFT_NODE_TYPE = "USER_TTFT"
USER_TTFT_NODE_NAME = "user-first-packet"


@dataclass
class RagTraceProperties:
    """RAG Trace 配置（对齐 Java RagTraceProperties，prefix rag.trace）"""

    enabled: bool = True          # 是否启用追踪采集
    max_error_length: int = 1000  # 错误信息最大长度（防落库过大）


# ==================== 追踪上下文（contextvars） ====================


class RagTraceContext:
    """RAG 追踪上下文（对齐 Java RagTraceContext）：contextvars 承载 trace_id / task_id / 节点栈"""

    _trace_id: contextvars.ContextVar = contextvars.ContextVar("rag_trace_id", default=None)
    _task_id: contextvars.ContextVar = contextvars.ContextVar("rag_task_id", default=None)
    # 节点栈用不可变 tuple：子任务经 create_task 拷贝 context 拿到的是 tuple 快照引用，
    # 后续 push/pop 重建新 tuple，父子栈互不串挂（对齐 Java TTL 深拷贝 ArrayDeque）
    _node_stack: contextvars.ContextVar = contextvars.ContextVar("rag_node_stack", default=())

    # ---------- trace_id ----------

    @classmethod
    def get_trace_id(cls) -> Optional[str]:
        return cls._trace_id.get()

    @classmethod
    def set_trace_id(cls, trace_id: str) -> None:
        cls._trace_id.set(trace_id)

    # ---------- task_id ----------

    @classmethod
    def get_task_id(cls) -> Optional[str]:
        return cls._task_id.get()

    @classmethod
    def set_task_id(cls, task_id: str) -> None:
        cls._task_id.set(task_id)

    # ---------- 节点栈 ----------

    @classmethod
    def depth(cls) -> int:
        return len(cls._node_stack.get())

    @classmethod
    def current_node_id(cls) -> Optional[str]:
        stack = cls._node_stack.get()
        return stack[-1] if stack else None

    @classmethod
    def push_node(cls, node_id: str) -> None:
        cls._node_stack.set(cls._node_stack.get() + (node_id,))

    @classmethod
    def pop_node(cls) -> None:
        stack = cls._node_stack.get()
        if not stack:
            return
        cls._node_stack.set(stack[:-1])

    # ---------- 清理 ----------

    @classmethod
    def clear(cls) -> None:
        cls._trace_id.set(None)
        cls._task_id.set(None)
        cls._node_stack.set(())


# ==================== 透传装饰器 ====================


class ForwardingStreamCallback:
    """
    透传式 StreamCallback 装饰器（对齐 Java ForwardingStreamCallback）

    on_content / on_thinking 等直接透传 delegate；on_complete / on_error 透传后 **CAS-once**
    触发 on_finish(success, error)，便于 trace 等在流式终态只收尾一次。
    所有回调方法为 async（对齐 Python StreamCallback）。
    """

    def __init__(self, delegate):
        self._delegate = delegate
        self._finished = False
        self._first_content_seen = False

    @property
    def finished(self) -> bool:
        """是否已触发终态收尾（供编排层判断是否需要 finish_externally）"""
        return self._finished

    # ---------- 透传：首包钩子 ----------

    async def on_start(self) -> None:
        await self._delegate.on_start()

    async def on_content(self, content: str) -> None:
        if not self._first_content_seen:
            self._first_content_seen = True
            try:
                self.on_first_content()
            except Exception:  # noqa: BLE001 —— 钩子异常不能影响正常推流
                logger.warning("on_first_content 钩子异常: %s", exc_info=True)
        await self._delegate.on_content(content)

    async def on_thinking(self, content: str) -> None:
        await self._delegate.on_thinking(content)

    async def on_reply_to_message_id(self, message_id: str) -> None:
        await self._delegate.on_reply_to_message_id(message_id)

    async def on_sources(self, sources) -> None:
        await self._delegate.on_sources(sources)

    async def on_grounding_chunks(self, chunks) -> None:
        await self._delegate.on_grounding_chunks(chunks)

    # ---------- 终态：透传 + CAS-once 收尾 ----------

    async def on_complete(self) -> None:
        error: Optional[Exception] = None
        try:
            await self._delegate.on_complete()
        except Exception as ex:  # noqa: BLE001 —— 完成期异常不能误记为 SUCCESS
            error = ex
            raise
        finally:
            # 感知 delegate 是否抛异常：成功≠SUCCESS；异常以 ERROR 收尾（防口径失真）
            self._finish_once(error is None, error)

    async def on_error(self, error: Exception) -> None:
        try:
            await self._delegate.on_error(error)
        finally:
            self._finish_once(False, error)

    def finish_externally(self, success: bool, error: Optional[Exception] = None) -> None:
        """外部路径（如 cancel）触发收尾，不再透传 delegate"""
        self._finish_once(success, error)

    # ---------- 内部 ----------

    def _finish_once(self, success: bool, error: Optional[Exception]) -> None:
        if self._finished:
            return
        self._finished = True
        self.on_finish(success, error)

    # ---------- 子类钩子 ----------

    def on_first_content(self) -> None:
        """流式响应到达首个 onContent 时触发一次（常用于记录用户感知首包 TTFT）；默认空实现"""
        pass

    def on_finish(self, success: bool, error: Optional[Exception]) -> None:
        """流式终态收尾，仅触发一次；子类实现"""
        raise NotImplementedError  # pragma: no cover 子类必须实现


# ==================== 追踪入口包装 ====================

Callback = Any  # StreamCallback 实现


class StreamChatTraceRunner:
    """流式对话 Trace 包装器（对齐 Java StreamChatTraceRunner）"""

    def __init__(
        self,
        trace_properties: Optional[RagTraceProperties] = None,
        record_service: Optional[Any] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._properties = trace_properties or RagTraceProperties()
        self._record_service = record_service
        self._clock = clock or time.time

    async def run(
        self,
        question: str,
        conversation_id: Optional[str],
        task_id: str,
        user_id: Optional[str],
        callback: Callback,
        business_logic: Callable[[Callback], Awaitable[None]],
    ) -> None:
        """执行带追踪包装的业务逻辑（对齐 Java run；business_logic 收 traceAwareCallback，负责启动 pipeline）"""
        if not self._properties.enabled or self._record_service is None:
            await self._run_without_trace(conversation_id, task_id, callback, business_logic)
            return

        trace_id = str(default_generator.next_id())
        start_millis = int(self._clock() * 1000)
        run_start_time = now_iso()

        try:
            self._record_service.start_run(self._build_run_row(
                trace_id, question, conversation_id, task_id, user_id, run_start_time
            ))
        except Exception as ex:  # noqa: BLE001 —— 追踪落库失败不阻断业务
            logger.warning("startRun 失败，traceId=%s: %s", trace_id, ex)

        trace_aware = _TraceAwareCallback(
            self, callback, trace_id, run_start_time, start_millis
        )

        RagTraceContext.set_trace_id(trace_id)
        RagTraceContext.set_task_id(task_id)
        try:
            await business_logic(trace_aware)
        except BaseException as ex:  # noqa: BLE001 —— 含 CancelledError（用户 stop → task.cancel）
            is_cancel = isinstance(ex, asyncio.CancelledError)
            logger.warning("执行流式对话失败（同步阶段），会话ID=%s，任务ID=%s", conversation_id, task_id, exc_info=True)
            if not trace_aware.finished:
                # trace 职责内聚收尾（对应 finish_externally 口子）：异常/取消均以 ERROR 记录，
                # 不依赖编排层经闭包转发 traceAware——避免 cancel 后 finish_run 永不执行导致 RUNNING 悬挂
                trace_aware.finish_externally(False, ex)
            try:
                await trace_aware.on_error(ex)
            except BaseException:  # noqa: BLE001 —— onError 失败也要保证收尾
                pass
            if is_cancel:
                raise  # 取消需继续上抛（3.6 依赖其做用户侧补偿 + INTERRUPTED 落库）
        finally:
            RagTraceContext.clear()

    # ==================== 内部 ====================

    def _build_run_row(self, trace_id, question, conversation_id, task_id, user_id, run_start_time) -> dict:
        return {
            "trace_id": trace_id,
            "trace_name": TRACE_NAME,
            "entry_method": ENTRY_METHOD,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "user_id": user_id,
            "status": STATUS_RUNNING,
            "start_time": run_start_time,
            "extra_data": json.dumps({"questionLength": len(question), "question": question}, ensure_ascii=False),
        }

    def _record_user_ttft(self, trace_id, run_start_time, start_millis) -> None:
        """记录用户感知首包 TTFT（对齐 Java recordUserTtft）：run 开始 → 首个 onContent"""
        now = int(self._clock() * 1000)
        duration_ms = max(0, now - start_millis)
        node_id = str(default_generator.next_id())
        try:
            self._record_service.start_node({
                "trace_id": trace_id,
                "node_id": node_id,
                "depth": 0,
                "node_type": USER_TTFT_NODE_TYPE,
                "node_name": USER_TTFT_NODE_NAME,
                "status": STATUS_RUNNING,
                "start_time": run_start_time,
            })
            self._record_service.finish_node(trace_id, node_id, STATUS_SUCCESS, now_iso(), duration_ms)
        except Exception as ex:  # noqa: BLE001
            logger.warning("写入 user-first-packet 节点失败，traceId=%s: %s", trace_id, ex)

    def _finish_run(self, trace_id, success, error, start_millis) -> None:
        try:
            end = int(self._clock() * 1000)
            self._record_service.finish_run(
                trace_id,
                STATUS_SUCCESS if success else STATUS_ERROR,
                None if success else self._truncate_error(error),
                now_iso(),
                end - start_millis,
            )
        except Exception as ex:  # noqa: BLE001
            logger.warning("finishRun 失败，traceId=%s: %s", trace_id, ex)

    def _truncate_error(self, error) -> Optional[str]:
        if error is None:
            return None
        message = error.__class__.__name__ + ": " + (error.__str__() or "")
        limit = self._properties.max_error_length
        return message if len(message) <= limit else message[:limit]

    async def _run_without_trace(self, conversation_id, task_id, callback, business_logic) -> None:
        try:
            await business_logic(callback)
        except Exception as ex:  # noqa: BLE001
            logger.warning("执行流式对话失败，会话ID=%s，任务ID=%s", conversation_id, task_id, exc_info=True)
            await callback.on_error(ex)


class _TraceAwareCallback(ForwardingStreamCallback):
    """追踪增强的透传回调（对齐 Java run 内匿名子类）：首包记 TTFT；终态收尾 finish_run"""

    def __init__(self, runner, delegate, trace_id, run_start_time, start_millis):
        super().__init__(delegate)
        self._runner = runner
        self._trace_id = trace_id
        self._run_start_time = run_start_time
        self._start_millis = start_millis

    def on_first_content(self) -> None:
        self._runner._record_user_ttft(self._trace_id, self._run_start_time, self._start_millis)

    def on_finish(self, success: bool, error: Optional[Exception]) -> None:
        self._runner._finish_run(self._trace_id, success, error, self._start_millis)