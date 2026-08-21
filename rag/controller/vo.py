# -*- coding: utf-8 -*-
"""
rag.controller.vo - P4 controller 视图模型（pydantic v2，对应 Java controller/vo/*）

方案 B：service 层保持 Python 惯用 snake_case，本模块用 pydantic + `alias` 在 controller
边界统一转为 camelCase（对齐 Java VO 字段，如 createTime/updateTime），与 M2 已交付的
message VO 命名风格保持一致。序列化统一走 `model_dump(by_alias=True)` 得 camelCase dict。

集合 C13：13 个 VO 随各域切片追加（本文件收纳 SampleQuestionVO / ConversationVO / ConversationMessageVO；
M2 的会话 VOs 已随 C2/C3 camelCase 修复迁移完成）。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


def camelize(value):
    """把 service 的 snake_case dict/list 递归转成 camelCase（供深层嵌套 VO，如追踪/Agent/意图树/设置/图谱）。

    方案 B 边界转换的统一实现：单层扁平 VO 用 pydantic `alias`（见 SampleQuestionVO），
    深层嵌套结构用本递归转换，避免为每层手写 alias 模型。
    """
    if isinstance(value, dict):
        return {_to_camel(k): camelize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [camelize(item) for item in value]
    return value


def _to_camel(key: str) -> str:
    """snake_case 键 → camelCase（保留非标识符形态；'id' 等原样）"""
    if not isinstance(key, str):
        return key
    parts = key.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class SampleQuestionVO(BaseModel):
    """示例问题视图（对应 Java SampleQuestionVO；序列化键为 camelCase）"""

    model_config = ConfigDict(populate_by_name=True)  # 允许按 snake_case 字段名构造

    id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    question: Optional[str] = None
    create_time: Optional[str] = Field(default=None, alias="createTime")
    update_time: Optional[str] = Field(default=None, alias="updateTime")

    @classmethod
    def from_row(cls, row: dict) -> "SampleQuestionVO":
        """service 的 snake_case dict → VO（缺失键留默认 None）"""
        return cls(
            id=row.get("id"),
            title=row.get("title"),
            description=row.get("description"),
            question=row.get("question"),
            create_time=row.get("create_time"),
            update_time=row.get("update_time"),
        )

    def to_camel_dict(self) -> dict:
        """序列化为 camelCase JSON dict（对齐 Java VO 字段名）"""
        return self.model_dump(by_alias=True)


class ConversationVO(BaseModel):
    """会话视图（对应 Java ConversationVO；序列化键为 camelCase，仅投影 conversationId/title/lastTime）"""

    model_config = ConfigDict(populate_by_name=True)  # 允许按 snake_case 字段名构造

    conversation_id: Optional[str] = Field(default=None, alias="conversationId")
    title: Optional[str] = None
    last_time: Optional[str] = Field(default=None, alias="lastTime")

    @classmethod
    def from_row(cls, row: dict) -> "ConversationVO":
        """service 的 snake_case 行 → VO（仅取展示字段，投影掉 userId/deleted 等内部列）"""
        return cls(
            conversation_id=row.get("conversation_id"),
            title=row.get("title"),
            last_time=row.get("last_time"),
        )

    def to_camel_dict(self) -> dict:
        """序列化为 camelCase JSON dict（对齐 Java VO 字段名）"""
        return self.model_dump(by_alias=True)


class ConversationMessageVO(BaseModel):
    """会话消息视图（对应 Java ConversationMessageVO；序列化键为 camelCase）

    sources 为 SourceRef dict（to_dict 已 camelCase，原样透传）；recommended_questions 为字符串列表。
    """

    model_config = ConfigDict(populate_by_name=True)  # 允许按 snake_case 字段名构造

    id: Optional[str] = None
    conversation_id: Optional[str] = Field(default=None, alias="conversationId")
    role: Optional[str] = None
    content: Optional[str] = None
    thinking_content: Optional[str] = Field(default=None, alias="thinkingContent")
    thinking_duration: Optional[int] = Field(default=None, alias="thinkingDuration")
    vote: Optional[int] = None
    sources: Optional[List[dict]] = None
    recommended_questions: Optional[List[str]] = Field(default=None, alias="recommendedQuestions")
    message_status: Optional[str] = Field(default=None, alias="messageStatus")
    create_time: Optional[str] = Field(default=None, alias="createTime")

    @classmethod
    def from_row(cls, vo: dict) -> "ConversationMessageVO":
        """service 的 snake_case VO dict → 模型（缺失键留默认 None）"""
        return cls(
            id=vo.get("id"),
            conversation_id=vo.get("conversation_id"),
            role=vo.get("role"),
            content=vo.get("content"),
            thinking_content=vo.get("thinking_content"),
            thinking_duration=vo.get("thinking_duration"),
            vote=vo.get("vote"),
            sources=vo.get("sources"),
            recommended_questions=vo.get("recommended_questions"),
            message_status=vo.get("message_status"),
            create_time=vo.get("create_time"),
        )

    def to_camel_dict(self) -> dict:
        """序列化为 camelCase JSON dict（对齐 Java VO 字段名）"""
        return self.model_dump(by_alias=True)