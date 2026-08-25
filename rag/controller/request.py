# -*- coding: utf-8 -*-
"""
rag.controller.request - P4 controller 请求模型（pydantic v2，对应 Java controller/request/*）

方案 B：请求体在 controller 边界用 pydantic 声明（service 层不感知 HTTP 传输模型）。
集合 C13：16 个 request DTO 随各域切片追加（本文件先收纳 M4 三个）。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


# ==================== 示例问题（对应 SampleQuestionCreateRequest / UpdateRequest） ====================


class SampleQuestionCreateRequest(BaseModel):
    """创建示例问题请求（question 必填；title/description 可选）"""

    title: Optional[str] = None
    description: Optional[str] = None
    question: str


class SampleQuestionUpdateRequest(BaseModel):
    """更新示例问题请求（仅传需更新的字段，None 表示不更新）"""

    title: Optional[str] = None
    description: Optional[str] = None
    question: Optional[str] = None


# ==================== 消息反馈（对应 MessageFeedbackRequest） ====================


class MessageFeedbackRequest(BaseModel):
    """消息反馈提交请求（vote 1=赞/-1=踩，reason/comment 可选）"""

    vote: Optional[int] = None
    reason: Optional[str] = None
    comment: Optional[str] = None


# ==================== 追踪查询（对应 RagTraceRunPageRequest） ====================


class TraceRunPageRequest(BaseModel):
    """追踪运行分页请求（query params；各过滤字段可选）"""

    current: Optional[int] = None
    size: Optional[int] = None
    trace_id: Optional[str] = None
    conversation_id: Optional[str] = None
    task_id: Optional[str] = None
    status: Optional[str] = None


# ==================== 术语映射管理（对应 QueryTermMappingCreateRequest / UpdateRequest） ====================


class QueryTermMappingCreateRequest(BaseModel):
    """创建术语映射（sourceTerm/targetTerm 必填）"""

    source_term: Optional[str] = None
    target_term: Optional[str] = None
    match_type: Optional[int] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    remark: Optional[str] = None


class QueryTermMappingUpdateRequest(BaseModel):
    """更新术语映射（仅传需更新字段）"""

    source_term: Optional[str] = None
    target_term: Optional[str] = None
    match_type: Optional[int] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    remark: Optional[str] = None


# ==================== Agent 档案 / 提示词（对应 AgentProfileSaveRequest / AgentPromptSaveRequest） ====================


class AgentProfileSaveRequest(BaseModel):
    """保存智能体档案（PUT 全量：name 必传，desc/avatar 可空）"""

    name: Optional[str] = None
    description: Optional[str] = None
    avatar: Optional[str] = None


class AgentPromptSaveRequest(BaseModel):
    """保存槽位提示词（content 空白即恢复回落）"""

    content: Optional[str] = None


# ==================== 意图节点（对应 IntentNodeCreateRequest / UpdateRequest / BatchRequest） ====================


class IntentNodeCreateRequest(BaseModel):
    """创建意图节点（intentCode/name 必填）"""

    intent_code: str
    name: str
    level: Optional[int] = None
    kind: Optional[int] = None
    parent_code: Optional[str] = None
    description: Optional[str] = None
    collection_names: Optional[List[str]] = None
    kb_id: Optional[str] = None
    mcp_tool_id: Optional[str] = None
    examples: Optional[List[str]] = None
    top_k: Optional[int] = None
    sort_order: Optional[int] = None
    enabled: Optional[int] = None
    param_prompt_template: Optional[str] = None
    prompt_snippet: Optional[str] = None
    prompt_template: Optional[str] = None


class IntentNodeUpdateRequest(BaseModel):
    """更新意图节点（仅传需更新字段）"""

    name: Optional[str] = None
    level: Optional[int] = None
    parent_code: Optional[str] = None
    description: Optional[str] = None
    collection_names: Optional[List[str]] = None
    collection_name: Optional[str] = None
    mcp_tool_id: Optional[str] = None
    examples: Optional[List[str]] = None
    top_k: Optional[int] = None
    kind: Optional[int] = None
    sort_order: Optional[int] = None
    enabled: Optional[int] = None
    param_prompt_template: Optional[str] = None
    prompt_snippet: Optional[str] = None
    prompt_template: Optional[str] = None


class IntentNodeBatchRequest(BaseModel):
    """批量操作请求（ids 必填）"""

    ids: List[str]


# ==================== Agent 对话（POST /agent/chat，P3 前端 Phase 0 补 Pydantic 校验） ====================


class AgentTurn(BaseModel):
    """Agent 对话历史单轮（role ∈ user/assistant，service 层 _to_messages 消费）"""

    role: str
    content: str


class AgentChatRequest(BaseModel):
    """POST /agent/chat 请求体：question + 可选 history（history 项结构化校验）

    question 用默认空串而非必填：空值/缺失由 controller 显式 400（保留既有语义，
    避免 Pydantic 必填把缺失变 422）。
    """

    question: str = ""
    history: Optional[List[AgentTurn]] = None