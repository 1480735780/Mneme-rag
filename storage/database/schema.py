"""
关系库表结构规格（DDL 边界，对应 Java DO → 表 / Flyway 迁移语义）

DatabaseClient.ensure_schema 的入参模型：以「表名 + 列（Postgres 方言类型）」表达
t_* DO 的表结构，供 in-memory（登记空表）与真实 SQL 后端（CREATE TABLE IF NOT EXISTS）
消费，表名/列对齐 Java DO 字段。

列类型为 Postgres 方言字符串（VARCHAR(n) / TIMESTAMP / INTEGER / JSONB），
真实后端据此生成 DDL；in-memory 仅取列名登记，类型不参与行为。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.entity.ConversationDO / ConversationMessageDO / ConversationSummaryDO
    - com.nageoffer.ai.ragent.knowledge.dao.entity.KnowledgeBaseDO
    - com.nageoffer.ai.ragent.rag.dao.entity.AgentProfileDO / AgentPromptDO
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class ColumnSpec:
    """
    表列规格（对应 Java DO 的一个字段）

    Attributes:
        name:        列名（snake_case）
        data_type:   SQL 类型（Postgres 方言，如 VARCHAR(64) / TIMESTAMP / INTEGER / JSONB）
        primary_key: 是否主键列（默认 False）
    """

    name: str
    data_type: str
    primary_key: bool = False

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("列名不能为空")
        if not self.data_type or not self.data_type.strip():
            raise ValueError(f"列 {self.name} 缺少 SQL 类型")


@dataclass(frozen=True)
class TableSchema:
    """
    表结构规格（对应 Java DO → 表）

    Attributes:
        name:    表名（t_*）
        columns: 列规格列表（顺序即 DDL 列序）
        comment: 表注释（可选）
    """

    name: str
    columns: Tuple[ColumnSpec, ...] = field(default_factory=tuple)
    comment: Optional[str] = None

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("表名不能为空")
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"表 {self.name} 存在重复列名")

    def column_names(self) -> Tuple[str, ...]:
        """列名序列（保序）"""
        return tuple(c.name for c in self.columns)


def _cols(*items: Tuple[str, str]) -> Tuple[ColumnSpec, ...]:
    """便捷构造：(name, data_type) 列表 → ColumnSpec 元组"""
    return tuple(ColumnSpec(name=n, data_type=t) for n, t in items)


# 各业务表默认结构（对齐 Java DO 字段语义，列类型为 Postgres 方言）
_T_CONVERSATION = TableSchema(
    name="t_conversation",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("conversation_id", "VARCHAR(64)"),
            ("user_id", "VARCHAR(64)"),
            ("title", "VARCHAR(200)"),
            ("last_time", "TIMESTAMP"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="会话",
)

_T_MESSAGE = TableSchema(
    name="t_message",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("conversation_id", "VARCHAR(64)"),
            ("user_id", "VARCHAR(64)"),
            ("role", "VARCHAR(16)"),
            ("content", "TEXT"),
            ("thinking_content", "TEXT"),
            ("thinking_duration", "INTEGER"),
            ("sources", "JSONB"),
            ("retrieved_chunks", "JSONB"),
            ("recommended_questions", "JSONB"),
            ("reply_to_message_id", "VARCHAR(32)"),
            ("message_status", "VARCHAR(16)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="会话消息",
)

_T_CONVERSATION_SUMMARY = TableSchema(
    name="t_conversation_summary",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("conversation_id", "VARCHAR(64)"),
            ("user_id", "VARCHAR(64)"),
            ("content", "TEXT"),
            ("last_message_id", "VARCHAR(32)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="会话摘要",
)

_T_KNOWLEDGE_BASE = TableSchema(
    name="t_knowledge_base",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("name", "VARCHAR(100)"),
            ("embedding_model", "VARCHAR(64)"),
            ("collection_name", "VARCHAR(64)"),
            ("created_by", "VARCHAR(64)"),
            ("updated_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="知识库",
)

_T_AGENT_PROFILE = TableSchema(
    name="t_agent_profile",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("name", "VARCHAR(100)"),
            ("description", "VARCHAR(500)"),
            ("avatar", "VARCHAR(64)"),
            ("builtin", "INTEGER"),
            ("active", "INTEGER"),
            ("create_by", "VARCHAR(64)"),
            ("update_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="智能体档案（builtin=1 内置 / active=1 激活）",
)

_T_AGENT_PROMPT = TableSchema(
    name="t_agent_prompt",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("agent_id", "VARCHAR(32)"),
            ("slot_key", "VARCHAR(64)"),
            ("content", "TEXT"),
            ("create_by", "VARCHAR(64)"),
            ("update_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="智能体提示词槽位（content 空白视为未配置并回落内置）",
)

# 当前消费方用到的全部 t_* 表（4.1 KB provider / 5.1 记忆 store / 摘要 / 5.5 AgentPromptResolver）
DEFAULT_TABLES: List[TableSchema] = [
    _T_CONVERSATION,
    _T_MESSAGE,
    _T_CONVERSATION_SUMMARY,
    _T_KNOWLEDGE_BASE,
    _T_AGENT_PROFILE,
    _T_AGENT_PROMPT,
]
