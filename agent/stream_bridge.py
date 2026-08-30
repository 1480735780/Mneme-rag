# -*- coding: utf-8 -*-
"""
agent.stream_bridge - agentscope 事件流到 SSE 协议的桥（对应 Java AgentStreamEventBridge）

增量转发、工具进度与结果、轨迹落库与取消收尾。轨迹块（AgentBlock）按事件序分段：
reasoning / answer 文本块在工具事件到来时封口，工具块 start→end 成对，结果文本截 20k。

**agentscope Python 事件面与 Java 的对应**（reply_stream(yield_final_msg=True)）：
    TextBlockDeltaEvent.delta        → response 增量
    ThinkingBlockDeltaEvent.delta    → think 增量
    ToolCallStartEvent               → 工具 start
    ToolResultTextDeltaEvent.delta   → 工具结果增量（攒缓冲，end 时截断定格）
    ToolResultEndEvent(state)        → 工具 end（done/failed）
    HintBlockEvent.hint              → HINT(AGENT_HINT)
    ReplyEndEvent.finished_reason == EXCEED_MAX_ITERS → HINT(MAX_ITERATIONS)（Java 的
      EXCEED_MAX_ITERS 事件在 Python 已废弃，改由 ReplyEndEvent 收尾原因承载）
    Msg（yield_final_msg 产出）       → 终答兜底文本（增量缺失时补发）

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.service.handler.AgentStreamEventBridge
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from agentscope.message import Msg, TextBlock, ToolResultState

from agent.models import (
    AgentBlock,
    AgentCompletionPayload,
    AgentHintPayload,
    AgentMessageDelta,
    AgentMessageStatus,
    AgentSSEEventType,
    AgentToolProgress,
)
from agent.run_handle import AgentRunHandle

logger = logging.getLogger(__name__)

DELTA_TYPE_RESPONSE = "response"
DELTA_TYPE_THINK = "think"
TOOL_STATUS_START = "start"
TOOL_STATUS_END = "end"
HINT_AGENT = "AGENT_HINT"
HINT_MAX_ITERATIONS = "MAX_ITERATIONS"
TOOL_RESULT_MAX_CHARS = 20_000
FALLBACK_CALL_KEY = "__anonymous__"
BLOCK_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class AgentStreamEventBridge:
    """事件 → SSE 增量桥（可变状态一律在 state_lock 下变更，SSE 发送在锁外）"""

    def __init__(
        self,
        *,
        run_handle: AgentRunHandle,
        conversation_service: Any,
        catalog: Any,
        conversation_id: str,
        user_id: str,
        title: str,
        reply_to_message_id: Optional[str],
    ):
        self._run_handle = run_handle
        self._sender = run_handle.sender
        self._conversation_service = conversation_service
        self._catalog = catalog
        self._conversation_id = conversation_id
        self._user_id = user_id
        self._title = title
        self._reply_to_message_id = reply_to_message_id

        self._state_lock = threading.Lock()
        self._response_buffer: List[str] = []
        self._thinking_buffer: List[str] = []
        self._result_msg: Optional[Msg] = None
        self._blocks: List[AgentBlock] = []
        self._open_tool_blocks: Dict[str, AgentBlock] = {}
        self._tool_result_buffers: Dict[str, List[str]] = {}
        self._open_text_block: Optional[AgentBlock] = None
        self._open_text_buffer: Optional[List[str]] = None

    # ==================== 事件分发 ====================

    def on_event(self, event: Any) -> None:
        """单事件处理（增量转发在锁外，慢消费者不阻塞事件解析）"""
        if isinstance(event, Msg):  # yield_final_msg 产出的终答
            with self._state_lock:
                self._result_msg = event
            return
        etype = getattr(event, "type", None)
        type_name = getattr(etype, "name", "")  # EventType 枚举 name（TEXT_BLOCK_DELTA 等）
        if type_name == "TEXT_BLOCK_DELTA":
            self._on_text_delta(event.delta, DELTA_TYPE_RESPONSE)
        elif type_name == "THINKING_BLOCK_DELTA":
            self._on_text_delta(event.delta, DELTA_TYPE_THINK)
        elif type_name == "TOOL_CALL_START":
            self._on_tool_start(event)
        elif type_name == "TOOL_RESULT_TEXT_DELTA":
            self._on_tool_result_delta(event)
        elif type_name == "TOOL_RESULT_END":
            self._on_tool_end(event)
        elif type_name == "HINT_BLOCK":
            self._on_hint(getattr(event, "hint", None))
        elif type_name == "REPLY_END":
            self._on_reply_end(event)
        # 其余事件类型（model_call_start/end、tool_call_delta 等）不映射 SSE

    # ==================== 文本增量 ====================

    def _on_text_delta(self, delta: Optional[str], delta_type: str) -> None:
        if not delta:
            return
        with self._state_lock:
            (self._thinking_buffer if delta_type == DELTA_TYPE_THINK else self._response_buffer).append(delta)
            self._append_text_block(delta_type, delta)
        self._sender.send_event(
            AgentSSEEventType.MESSAGE.value,
            AgentMessageDelta(delta_type, delta),
        )

    # ==================== 工具事件 ====================

    def _on_tool_start(self, event: Any) -> None:
        tool_name = getattr(event, "tool_call_name", None)
        if self._is_internal_tool(tool_name):
            return
        block = AgentBlock(
            kind="tool",
            at=datetime.now().strftime(BLOCK_TIME_FORMAT),
            name=tool_name,
            display_name=self._catalog.display_name_of(tool_name),
            status="running",
        )
        with self._state_lock:
            self._seal_open_text_block()
            self._blocks.append(block)
            self._open_tool_blocks[self._call_key(getattr(event, "tool_call_id", None))] = block
        self._sender.send_event(
            AgentSSEEventType.TOOL.value,
            AgentToolProgress(tool_name, block.display_name, TOOL_STATUS_START, None, None),
        )

    def _on_tool_result_delta(self, event: Any) -> None:
        # ToolResultTextDeltaEvent 不携带 tool_call_name（agentscope 事件面）：归属判定改走
        # start 时登记的 open block——内部工具在 start 即被跳过、没有登记块，其增量在这里天然丢弃
        delta = getattr(event, "delta", None)
        if not delta:
            return
        with self._state_lock:
            call_key = self._call_key(getattr(event, "tool_call_id", None))
            if call_key not in self._open_tool_blocks:
                return
            self._tool_result_buffers.setdefault(call_key, []).append(delta)

    def _on_tool_end(self, event: Any) -> None:
        # ToolResultEndEvent 同样不带 tool_call_name：名字/展示名取自 start 登记的块；
        # 无登记块的 end（内部工具、乱序事件）静默丢弃
        ok = getattr(event, "state", None) == ToolResultState.SUCCESS
        with self._state_lock:
            self._seal_open_text_block()
            call_key = self._call_key(getattr(event, "tool_call_id", None))
            buffer = self._tool_result_buffers.pop(call_key, None)
            result = "".join(buffer)[:TOOL_RESULT_MAX_CHARS] if buffer is not None else None
            block = self._open_tool_blocks.pop(call_key, None)
            if block is not None:
                block.status = "done" if ok else "failed"
                block.result = result
        if block is None:
            return
        self._sender.send_event(
            AgentSSEEventType.TOOL.value,
            AgentToolProgress(block.name, block.display_name, TOOL_STATUS_END, result, ok),
        )

    def _on_hint(self, hint: Optional[str]) -> None:
        if not hint or not str(hint).strip():
            return
        self._sender.send_event(
            AgentSSEEventType.HINT.value,
            AgentHintPayload(HINT_AGENT, str(hint)),
        )

    def _on_reply_end(self, event: Any) -> None:
        """达到迭代上限后框架仍会生成总结与终答，只提示不判失败（对齐 Java EXCEED_MAX_ITERS）"""
        from agentscope.types import ReplyFinishedReason

        reason = getattr(event, "finished_reason", None)
        if reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
            self._sender.send_event(
                AgentSSEEventType.HINT.value,
                AgentHintPayload(HINT_MAX_ITERATIONS, "已达到最大迭代次数，正在生成当前执行结果的总结"),
            )

    @staticmethod
    def _is_internal_tool(tool_name: Optional[str]) -> bool:
        """结构化输出兜底工具是框架内部实现细节，不作为业务工具进度暴露；空名同样跳过"""
        return not tool_name or tool_name == "GenerateStructuredOutput"

    @staticmethod
    def _call_key(tool_call_id: Optional[str]) -> str:
        """不规范端点可能不回 toolCallId，退化到单槽兜底（并发多路时轨迹会串，但不至于打断整条流）"""
        return tool_call_id if tool_call_id and tool_call_id.strip() else FALLBACK_CALL_KEY

    # ==================== 轨迹块分段 ====================

    def _append_text_block(self, delta_type: str, delta: str) -> None:
        """调用方需持 state_lock：增量只进缓冲，块文本到封口那一刻才定格"""
        kind = "reasoning" if delta_type == DELTA_TYPE_THINK else "answer"
        if self._open_text_block is None or kind != self._open_text_block.kind:
            self._seal_open_text_block()
            self._open_text_block = AgentBlock(kind=kind, at=datetime.now().strftime(BLOCK_TIME_FORMAT))
            self._open_text_buffer = []
            self._blocks.append(self._open_text_block)
        self._open_text_buffer.append(delta)

    def _seal_open_text_block(self) -> None:
        """调用方需持 state_lock：一次性落成 String（逐条 setText 是 O(n²) 全量复制）"""
        if self._open_text_block is None:
            return
        self._open_text_block.text = "".join(self._open_text_buffer or [])
        self._open_text_block = None
        self._open_text_buffer = None

    # ==================== 三条收尾路 ====================

    def complete(self) -> None:
        """正常收尾：流式增量为准（与用户所见一致），增量缺失回落终答文本并一次性补发"""
        def body() -> None:
            with self._state_lock:
                streamed = "".join(self._response_buffer)
            content = streamed if streamed.strip() else self._fallback_content()
            if not streamed and content.strip():
                with self._state_lock:
                    self._append_text_block(DELTA_TYPE_RESPONSE, content)
                self._sender.send_event(
                    AgentSSEEventType.MESSAGE.value,
                    AgentMessageDelta(DELTA_TYPE_RESPONSE, content),
                )
            message_id = self._persist_assistant_message(content, AgentMessageStatus.NORMAL)
            self._sender.send_event(
                AgentSSEEventType.FINISH.value,
                AgentCompletionPayload(message_id, self._title, AgentMessageStatus.NORMAL.value),
            )
            self._sender.send_event(AgentSSEEventType.DONE.value, "[DONE]")

        self._run_handle.complete(body)

    def fail(self, error: BaseException) -> None:
        """异常收尾：dispose 引发的信号中断不算错误，取消收尾由 finalizer 负责"""
        if self._run_handle.is_cancelled():
            return
        self._run_handle.fail(error, lambda: logger.error("Agent 流式会话异常, taskId: %s", self._run_handle.task_id, exc_info=error))

    def finish_cancelled_stream(self) -> None:
        """取消收尾：持久化已生成内容后补发 cancel/done 并结束响应流"""
        def body() -> None:
            with self._state_lock:
                content = "".join(self._response_buffer)
                tracked = bool(self._thinking_buffer) or bool(self._blocks)
            message_id = None
            if content.strip() or tracked:
                message_id = self._persist_assistant_message(content, AgentMessageStatus.INTERRUPTED)
            self._sender.send_event(
                AgentSSEEventType.CANCEL.value,
                AgentCompletionPayload(message_id, self._title, AgentMessageStatus.INTERRUPTED.value),
            )
            self._sender.send_event(AgentSSEEventType.DONE.value, "[DONE]")

        self._run_handle.cancel(body)

    # ==================== 落库 ====================

    def _fallback_content(self) -> str:
        result = self._result_msg
        if result is None:
            return ""
        text = result.get_text_content()
        return text or ""

    def _persist_assistant_message(self, content: str, status: AgentMessageStatus) -> Optional[str]:
        """思考文本与轨迹取自同一临界区，免得取消瞬间两者对不上号；落库失败不炸收尾"""
        with self._state_lock:
            thinking = "".join(self._thinking_buffer)
            settled = self._settled_blocks()
        try:
            return self._conversation_service.add_assistant_message(
                self._conversation_id, self._user_id, content, thinking, settled, self._reply_to_message_id, status
            )
        except Exception:  # noqa: BLE001 终答落库失败只记日志
            logger.error("Agent 终答落库失败, conversationId: %s", self._conversation_id, exc_info=True)
            return None

    def _settled_blocks(self) -> Optional[List[Dict[str, Any]]]:
        """调用方需持 state_lock：敞口文本块先封口，running 工具置 interrupted，剔除空文本块"""
        self._seal_open_text_block()
        settled: List[AgentBlock] = []
        for block in self._blocks:
            if block.kind != "tool" and not (block.text or "").strip():
                continue
            if block.status == "running":
                block.status = "interrupted"
            settled.append(block)
        return [b.to_dict() for b in settled] if settled else None
