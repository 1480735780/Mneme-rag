# -*- coding: utf-8 -*-
"""
agent.models - Agent 域 DTO 与枚举（对应 Java agent/dto/* 与 agent/enums/*）

- `AgentBlock`：运行轨迹块——一轮回复内按事件顺序排列的 reasoning / answer / tool 片段，
  随消息落库（t_agent_message.blocks JSONB）供历史回放还原时间线。
  与 content/thinking_content 有意不等价：剔空块、工具结果截 20k，用作正文会丢字。
- `AgentMessageStatus`：消息终态（与 rag 侧 MessageStatus 分立）。
- `AgentSSEEventType`：Agent 模式 SSE 事件协议，与 workflow 协议两套分立。
- 五类 SSE 载荷：to_dict() 输出 camelCase（对应 workflow 协议的键名约定，前端同一消费方式），
  None 字段省略（对应 Java @JsonInclude(NON_NULL)）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.agent.dto.AgentBlock / AgentMetaPayload / AgentMessageDelta
      / AgentToolProgress / AgentHintPayload / AgentCompletionPayload
    - com.nageoffer.ai.ragent.agent.enums.AgentMessageStatus / AgentSSEEventType
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class AgentMessageStatus(str, Enum):
    """消息终态（与 rag 侧 MessageStatus 分立；落库存 name 字符串）"""

    NORMAL = "NORMAL"          # 正常完成
    INTERRUPTED = "INTERRUPTED"  # 用户中断，内容为已生成的部分


class AgentSSEEventType(str, Enum):
    """Agent 模式 SSE 事件协议，与 workflow 协议两套分立（value 即 SSE event 名）"""

    META = "meta"        # 会话与任务元信息
    MESSAGE = "message"  # 增量消息（type: response / think）
    TOOL = "tool"        # 工具进度 {name, displayName, status: start|end, result, ok}
    HINT = "hint"        # 运行提示（如达到迭代上限的熔断预告），不落库
    FINISH = "finish"    # 回复完成
    DONE = "done"        # 流结束
    CANCEL = "cancel"    # 用户取消


# 轨迹块种类（AgentBlock.kind）
BLOCK_KIND_REASONING = "reasoning"
BLOCK_KIND_ANSWER = "answer"
BLOCK_KIND_TOOL = "tool"

# 工具块终态（AgentBlock.status）
TOOL_STATUS_DONE = "done"
TOOL_STATUS_INTERRUPTED = "interrupted"


@dataclass
class AgentBlock:
    """
    运行轨迹块：一轮回复内按事件顺序排列的 reasoning / answer / tool 片段

    Attributes:
        kind:        reasoning / answer / tool
        at:          产生时刻 ISO 时间戳；展示形式由前端定（历史数据可能是 HH:mm:ss，前端两种都认）
        text:        reasoning / answer 的正文
        name:        tool 名（仅 tool 块）
        display_name: tool 展示名（仅 tool 块）
        status:      tool 终态 done / interrupted（仅 tool 块）
        result:      tool 结果文本，超长截断（仅 tool 块）
    """

    kind: str
    at: Optional[str] = None
    text: Optional[str] = None
    name: Optional[str] = None
    display_name: Optional[str] = None
    status: Optional[str] = None
    result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """camelCase + None 省略（落库 JSONB 与前端回放同一形态）"""
        return _camel_dict(
            kind=self.kind,
            at=self.at,
            text=self.text,
            name=self.name,
            display_name=self.display_name,
            status=self.status,
            result=self.result,
        )


def _camel_dict(**values: Any) -> Dict[str, Any]:
    """snake_case kwargs → camelCase dict（None 值省略，对应 Java NON_NULL）"""
    result: Dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        first, *rest = key.split("_")
        camel = first + "".join(part.title() for part in rest)
        result[camel] = value
    return result


@dataclass(frozen=True)
class AgentMetaPayload:
    """SSE meta 载荷：会话与任务元信息"""

    conversation_id: str
    task_id: str

    def to_dict(self) -> Dict[str, Any]:
        return _camel_dict(conversation_id=self.conversation_id, task_id=self.task_id)


@dataclass(frozen=True)
class AgentMessageDelta:
    """SSE message 载荷：增量消息（type: response / think）"""

    type: str
    delta: str

    def to_dict(self) -> Dict[str, Any]:
        return _camel_dict(type=self.type, delta=self.delta)


@dataclass(frozen=True)
class AgentToolProgress:
    """SSE tool 载荷：工具进度"""

    name: str
    display_name: Optional[str] = None
    status: Optional[str] = None  # start / end
    result: Optional[str] = None
    ok: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return _camel_dict(
            name=self.name,
            display_name=self.display_name,
            status=self.status,
            result=self.result,
            ok=self.ok,
        )


@dataclass(frozen=True)
class AgentHintPayload:
    """SSE hint 载荷：运行提示（不落库）"""

    code: str
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return _camel_dict(code=self.code, text=self.text)


@dataclass(frozen=True)
class AgentCompletionPayload:
    """SSE finish 载荷：回复完成（message_status 为 AgentMessageStatus.name）"""

    message_id: Optional[str] = None
    title: Optional[str] = None
    message_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _camel_dict(
            message_id=self.message_id,
            title=self.title,
            message_status=self.message_status,
        )
