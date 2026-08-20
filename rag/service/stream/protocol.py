# -*- coding: utf-8 -*-
"""
rag.service.stream.protocol - SSE 事件协议模型（对齐 Java rag.dto + enums.SSEEventType）

纯数据类型层：定义 SSE 载荷契约（事件名 + 三载荷 + 序列化约定），供 event_handler /
chat_service / controller 与前端统一消费；不依赖 FastAPI / engine。

对齐 Java 源码：
    - SSEEventType（六事件 value 事件名）
    - MetaPayload（record conversationId, taskId）
    - MessageDelta（record type, delta；type ∈ think/response）
    - CompletionPayload（record messageId, title, sources, messageStatus）

序列化约定（对齐 Java）：
    - 键名 camelCase（conversationId / taskId / messageId / messageStatus）；
    - @JsonInclude(NON_NULL)：字段为 **None** 时省略该键（空 list 仍保留，对齐 Jackson——NON_NULL 只忽略 null）；
    - messageStatus 输出**大写枚举名**（Java MessageStatus 为无 value 枚举，Jackson 序列化 name；
      Python MessageStatus 值为小写 normal/interrupted/rejected，故取 .name）；
    - sources（List[SourceRef]）经 SourceRef.to_dict 注入（camelCase），随载荷 JSON 化。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dto.MetaPayload / MessageDelta / CompletionPayload
    - com.nageoffer.ai.ragent.rag.enums.SSEEventType
    - com.nageoffer.ai.ragent.framework.convention.ChatMessage.MessageStatus
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from core.llm.schema import MessageStatus, SourceRef

# 事件名字符串（对齐 Java SSEEventType.value()）
_META = "meta"
_MESSAGE = "message"
_FINISH = "finish"
_DONE = "done"
_CANCEL = "cancel"
_REJECT = "reject"

# Spring SseEmitter.completeWithError 隐式 error 事件名（Java SSEEventType 枚举无 ERROR，
# 出错帧由 Spring 渲染 event: error；Python 此处显式下发以便客户端区分「异常」与「正常结束」）
ERROR_EVENT = "error"

# MessageDelta.type 取值（对齐 Java StreamChatEventHandler TYPE_THINK / TYPE_RESPONSE）
TYPE_THINK = "think"
TYPE_RESPONSE = "response"

# 限流拒绝兜底文案（对齐 Java ChatQueueLimiter.REJECT_MESSAGE）
REJECT_MESSAGE = "系统繁忙，请稍后再试"

# 消息切块默认块大小（对齐 Java StreamChatEventHandler.resolveMessageChunkSize 缺省 5）
DEFAULT_MESSAGE_CHUNK_SIZE = 5


class SSEEventType(Enum):
    """SSE 事件类型（对应 Java SSEEventType，value 为前端约定的事件名）"""

    META = _META
    MESSAGE = _MESSAGE
    FINISH = _FINISH
    DONE = _DONE
    CANCEL = _CANCEL
    REJECT = _REJECT


@dataclass(frozen=True)
class MetaPayload:
    """会话与任务的元信息事件载荷（对应 Java record MetaPayload(conversationId, taskId)）"""

    conversation_id: str
    task_id: str

    def to_json(self) -> str:
        return json.dumps({"conversationId": self.conversation_id, "taskId": self.task_id})


@dataclass(frozen=True)
class MessageDelta:
    """增量消息事件载荷（对应 Java record MessageDelta(type, delta)；type ∈ think/response）"""

    type: str
    delta: str

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "delta": self.delta})


@dataclass(frozen=True)
class CompletionPayload:
    """
    模型回复完成事件载荷（对应 Java record CompletionPayload(messageId, title, sources, messageStatus)）

    NON_NULL 省略语义：message_id / title / sources 为 None 时省略键；
    message_status 输出大写枚举名。空 list 的 sources 仍保留（对齐 Jackson NON_NULL 只略 null）。
    """

    message_id: Optional[str] = None
    title: Optional[str] = None
    sources: Optional[List[SourceRef]] = None
    message_status: MessageStatus = MessageStatus.NORMAL

    def to_json(self) -> str:
        data: dict = {}
        if self.message_id is not None:
            data["messageId"] = self.message_id
        if self.title is not None:
            data["title"] = self.title
        if self.sources is not None:
            data["sources"] = [s.to_dict() for s in self.sources]
        # messageStatus 必含（无 None 场景）：大写枚举名，对齐 Java MessageStatus name 序列化
        data["messageStatus"] = self.message_status.name
        return json.dumps(data)