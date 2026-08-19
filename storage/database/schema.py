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
    - com.nageoffer.ai.ragent.rag.dao.entity.MessageFeedbackDO / SampleQuestionDO
    - com.nageoffer.ai.ragent.rag.dao.entity.RagTraceRunDO / RagTraceNodeDO
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

_T_INTENT_NODE = TableSchema(
    name="t_intent_node",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("kb_id", "VARCHAR(32)"),
            ("intent_code", "VARCHAR(64)"),
            ("name", "VARCHAR(100)"),
            ("level", "INTEGER"),
            ("parent_code", "VARCHAR(64)"),
            ("description", "VARCHAR(500)"),
            ("examples", "TEXT"),
            ("collection_name", "VARCHAR(64)"),
            ("collection_names", "TEXT"),
            ("mcp_tool_id", "VARCHAR(64)"),
            ("top_k", "INTEGER"),
            ("kind", "INTEGER"),
            ("sort_order", "INTEGER"),
            ("prompt_snippet", "TEXT"),
            ("prompt_template", "TEXT"),
            ("param_prompt_template", "TEXT"),
            ("enabled", "INTEGER"),
            ("create_by", "VARCHAR(64)"),
            ("update_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="意图树节点（enabled=1 启用 / deleted=0 未删除）",
)

_T_QUERY_TERM_MAPPING = TableSchema(
    name="t_query_term_mapping",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("domain", "VARCHAR(64)"),
            ("source_term", "VARCHAR(200)"),
            ("target_term", "VARCHAR(200)"),
            ("match_type", "INTEGER"),
            ("priority", "INTEGER"),
            ("enabled", "INTEGER"),
            ("remark", "VARCHAR(500)"),
            ("create_by", "VARCHAR(64)"),
            ("update_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
        ),
    ),
    comment="查询术语映射规则（enabled=1 生效；matchType=1 精确匹配；无 deleted 列——Java DO 无 @TableLogic，生命周期由 enabled 禁用控制）",
)

_T_MESSAGE_FEEDBACK = TableSchema(
    name="t_message_feedback",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("message_id", "VARCHAR(32)"),
            ("conversation_id", "VARCHAR(64)"),
            ("user_id", "VARCHAR(64)"),
            ("vote", "INTEGER"),
            ("reason", "VARCHAR(200)"),
            ("comment", "VARCHAR(500)"),
            ("submit_time", "BIGINT"),
            ("cancelled", "INTEGER"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="会话消息反馈（vote=1 点赞 / -1 点踩；submit_time 最新者生效，cancelled=1 取消）",
)

_T_SAMPLE_QUESTION = TableSchema(
    name="t_sample_question",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("title", "VARCHAR(200)"),
            ("description", "VARCHAR(500)"),
            ("question", "TEXT"),
            ("create_by", "VARCHAR(64)"),
            ("update_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="示例问题（运营配置，列表页随机抽样展示）",
)

_T_RAG_TRACE_RUN = TableSchema(
    name="t_rag_trace_run",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("trace_id", "VARCHAR(64)"),
            ("trace_name", "VARCHAR(64)"),
            ("entry_method", "VARCHAR(128)"),
            ("conversation_id", "VARCHAR(64)"),
            ("task_id", "VARCHAR(64)"),
            ("user_id", "VARCHAR(64)"),
            ("status", "VARCHAR(16)"),
            ("error_message", "TEXT"),
            ("start_time", "TIMESTAMP"),
            ("end_time", "TIMESTAMP"),
            ("duration_ms", "INTEGER"),
            ("extra_data", "JSONB"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="RAG 链路追踪运行汇总（status: RUNNING/SUCCESS/ERROR；extra_data 为 JSON；@TableLogic 软删）",
)

_T_RAG_TRACE_NODE = TableSchema(
    name="t_rag_trace_node",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("trace_id", "VARCHAR(64)"),
            ("node_id", "VARCHAR(64)"),
            ("parent_node_id", "VARCHAR(64)"),
            ("depth", "INTEGER"),
            ("node_type", "VARCHAR(32)"),
            ("node_name", "VARCHAR(64)"),
            ("class_name", "VARCHAR(128)"),
            ("method_name", "VARCHAR(128)"),
            ("status", "VARCHAR(16)"),
            ("error_message", "TEXT"),
            ("start_time", "TIMESTAMP"),
            ("end_time", "TIMESTAMP"),
            ("duration_ms", "INTEGER"),
            ("extra_data", "JSONB"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="RAG 追踪节点（方法级/流式 span；status: RUNNING/SUCCESS/ERROR；@TableLogic 软删）",
)

_T_KNOWLEDGE_CHUNK = TableSchema(
    name="t_knowledge_chunk",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("kb_id", "VARCHAR(32)"),
            ("doc_id", "VARCHAR(32)"),
            ("chunk_index", "INTEGER"),
            ("content", "TEXT"),
            ("content_hash", "VARCHAR(64)"),
            ("char_count", "INTEGER"),
            ("token_count", "INTEGER"),
            ("embedding_text", "TEXT"),
            ("enabled", "INTEGER"),
            ("created_by", "VARCHAR(64)"),
            ("updated_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="知识库文档分块",
)

_T_KNOWLEDGE_DOCUMENT = TableSchema(
    name="t_knowledge_document",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("kb_id", "VARCHAR(32)"),
            ("doc_name", "VARCHAR(200)"),
            ("source_type", "VARCHAR(16)"),
            ("source_location", "VARCHAR(512)"),
            ("schedule_enabled", "INTEGER"),
            ("schedule_cron", "VARCHAR(64)"),
            ("enabled", "INTEGER"),
            ("chunk_count", "INTEGER"),
            ("file_url", "VARCHAR(512)"),
            ("file_type", "VARCHAR(32)"),
            ("mime_type", "VARCHAR(128)"),
            ("file_size", "BIGINT"),
            ("process_mode", "VARCHAR(16)"),
            ("ingestion_spec", "TEXT"),
            ("pipeline_id", "VARCHAR(32)"),
            ("status", "VARCHAR(16)"),
            ("created_by", "VARCHAR(64)"),
            ("updated_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="知识库文档",
)

# 当前消费方用到的全部 t_* 表（4.1 KB provider / 5.1 记忆 store / 摘要 / 5.5 AgentPromptResolver / ChunkMetadataResolver
# / P4 会话/反馈/示例问题/追踪持久化）
DEFAULT_TABLES: List[TableSchema] = [
    _T_CONVERSATION,
    _T_MESSAGE,
    _T_CONVERSATION_SUMMARY,
    _T_KNOWLEDGE_BASE,
    _T_KNOWLEDGE_CHUNK,
    _T_KNOWLEDGE_DOCUMENT,
    _T_AGENT_PROFILE,
    _T_AGENT_PROMPT,
    _T_INTENT_NODE,
    _T_QUERY_TERM_MAPPING,
    _T_MESSAGE_FEEDBACK,
    _T_SAMPLE_QUESTION,
    _T_RAG_TRACE_RUN,
    _T_RAG_TRACE_NODE,
]
