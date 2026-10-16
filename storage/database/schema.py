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
    - com.nageoffer.ai.ragent.agent.dao.entity.AgentConversationDO / AgentMessageDO（v1.1 Agent 执行架构域）
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
class IndexSpec:
    """
    表索引规格（D2 增量对齐：CREATE [UNIQUE] INDEX IF NOT EXISTS，含部分索引）

    Attributes:
        name:    索引名（跨表全局唯一，对齐 PG 命名空间）
        columns: 索引列（保序）
        unique:  是否唯一索引（默认 False）
        where:   部分索引谓词（Postgres 方言原样拼接，如
                 "status IN ('QUEUED', 'RUNNING', 'BUILT_AWAITING_CONFIRMATION')"）；None = 全量索引
    """

    name: str
    columns: Tuple[str, ...]
    unique: bool = False
    where: Optional[str] = None


@dataclass(frozen=True)
class TableSchema:
    """
    表结构规格（对应 Java DO → 表）

    Attributes:
        name:    表名（t_*）
        columns: 列规格列表（顺序即 DDL 列序）
        comment: 表注释（可选）
        indexes: 索引规格列表（D2 增量对齐建；仅真实 SQL 后端消费，InMemory 忽略）
    """

    name: str
    columns: Tuple[ColumnSpec, ...] = field(default_factory=tuple)
    comment: Optional[str] = None
    indexes: Tuple[IndexSpec, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("表名不能为空")
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"表 {self.name} 存在重复列名")

    def column_names(self) -> Tuple[str, ...]:
        """列名序列（保序）"""
        return tuple(c.name for c in self.columns)

    def index_names(self) -> Tuple[str, ...]:
        """索引名序列（保序）"""
        return tuple(i.name for i in self.indexes)


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
            # V2.1.1 P-01: 摘要 schema 版本；NULL/'v1'=自由文本走 V1 路径，'v2'=Structured ConversationState
            ("summary_version", "VARCHAR(16)"),
            # V2.1.1 P-01: ConversationState 的 JSON 快照；V1 行为 NULL
            ("structured_content", "JSONB"),
            # V2.1.1 P-09: 本轮 LLM 调用次数（1=单次成功/失败, 0=L3 skip, >1 保留给未来 retry）
            ("attempt_count", "INTEGER"),
            # V2.1.1 P-09: 走的自愈档位，供 dashboard 识别"哪些会话正在挣扎"
            ("healing_level", "VARCHAR(32)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="会话摘要（V2.1.1 P-01 起支持 4-slot 结构化状态；content 保留文本快照兼容 V1 消费者）",
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
            # P9 阶段 0b（§17.2）：per-KB 版本化开关——未开启走旧路径，开启触发一次性 v0 迁移
            ("versioning_enabled", "BOOLEAN"),
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
            # P10.2 T2.1（D8）：激活指针列（P9 D2 增量对齐自动加列）。
            # NULL = 未版本化（回落 content 列，兼容存量数据）；激活/回滚 = 切指针同路径
            ("active_version_id", "VARCHAR(32)"),
            ("create_by", "VARCHAR(64)"),
            ("update_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="智能体提示词槽位（content 空白视为未配置并回落内置；active_version_id=NULL 时回落 content）",
)

# P10.2 T2.1（D8）：不可变版本表——保存 = 新版本行（永不 UPDATE 内容），生效 = 激活指针。
# 无 deleted/update 列（不可变语义由列集与接口缺失共同表达，与 t_llm_call append-only 同纪律）
_T_AGENT_PROMPT_VERSION = TableSchema(
    name="t_agent_prompt_version",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("agent_id", "VARCHAR(32)"),
            ("slot_key", "VARCHAR(64)"),
            ("version_no", "INTEGER"),
            ("content", "TEXT"),
            ("create_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
        ),
    ),
    indexes=(
        IndexSpec(
            name="idx_agent_prompt_version_slot",
            columns=("agent_id", "slot_key", "version_no"),
        ),
    ),
    comment="智能体提示词版本表（只追加，激活入 t_agent_prompt.active_version_id 指针）",
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
            ("parent_id", "VARCHAR(32)"),
            ("chunk_role", "VARCHAR(20)"),
            ("enabled", "INTEGER"),
            ("created_by", "VARCHAR(64)"),
            ("updated_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
            # P9 阶段 0b/1（§3.4）：chunk 归属的 revision / attempt（存量行 NULL = v0 迁移后回填）
            ("revision_id", "VARCHAR(32)"),
            ("build_attempt_id", "VARCHAR(32)"),
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
            # P9 阶段 0b/1（§3.1）：版本化指针与策略（存量行 NULL = 未迁移，走旧路径）
            ("active_revision_id", "VARCHAR(32)"),
            ("desired_revision_id", "VARCHAR(32)"),
            ("update_policy", "VARCHAR(16)"),
            ("source_identity", "VARCHAR(128)"),
            ("metadata_generation", "BIGINT"),
        ),
    ),
    comment="知识库文档",
)

# ==================== P5 E0 0.1：摄取/调度域 7 张新表（对齐 schema_pg.sql 列名，E1 dao 数据源） ====================


def _pk_col(name: str = "id") -> ColumnSpec:
    """主键列（对齐既有表：VARCHAR(32)）"""
    return ColumnSpec(name=name, data_type="VARCHAR(32)", primary_key=True)


_T_KNOWLEDGE_DOCUMENT_CHUNK_LOG = TableSchema(
    name="t_knowledge_document_chunk_log",
    columns=(
        _pk_col(),
        *_cols(
            ("doc_id", "VARCHAR(32)"),
            ("status", "VARCHAR(16)"),
            ("process_mode", "VARCHAR(16)"),
            ("parse_profile", "VARCHAR(16)"),
            ("pipeline_id", "VARCHAR(32)"),
            ("extract_duration", "BIGINT"),
            ("chunk_duration", "BIGINT"),
            ("embed_duration", "BIGINT"),
            ("persist_duration", "BIGINT"),
            ("total_duration", "BIGINT"),
            ("chunk_count", "INTEGER"),
            ("error_message", "TEXT"),
            ("start_time", "TIMESTAMP"),
            ("end_time", "TIMESTAMP"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
        ),
    ),
    comment="知识库文档分块日志表（chunk-logs 12 端点之一的数据源）",
)

_T_KNOWLEDGE_DOCUMENT_SCHEDULE = TableSchema(
    name="t_knowledge_document_schedule",
    columns=(
        _pk_col(),
        *_cols(
            ("doc_id", "VARCHAR(32)"),
            ("kb_id", "VARCHAR(32)"),
            ("cron_expr", "VARCHAR(64)"),
            ("enabled", "INTEGER"),
            ("next_run_time", "TIMESTAMP"),
            ("last_run_time", "TIMESTAMP"),
            ("last_success_time", "TIMESTAMP"),
            ("last_status", "VARCHAR(16)"),
            ("last_error", "VARCHAR(512)"),
            ("last_etag", "VARCHAR(256)"),
            ("last_modified", "VARCHAR(256)"),
            ("last_content_hash", "VARCHAR(128)"),
            ("lock_owner", "VARCHAR(128)"),
            ("lock_until", "TIMESTAMP"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
        ),
    ),
    comment="知识库文档定时刷新任务表（ScheduleJob scan 扫描对象）",
)

_T_KNOWLEDGE_DOCUMENT_SCHEDULE_EXEC = TableSchema(
    name="t_knowledge_document_schedule_exec",
    columns=(
        _pk_col(),
        *_cols(
            ("schedule_id", "VARCHAR(32)"),
            ("doc_id", "VARCHAR(32)"),
            ("kb_id", "VARCHAR(32)"),
            ("status", "VARCHAR(16)"),
            ("message", "VARCHAR(512)"),
            ("start_time", "TIMESTAMP"),
            ("end_time", "TIMESTAMP"),
            ("file_name", "VARCHAR(512)"),
            ("file_size", "BIGINT"),
            ("content_hash", "VARCHAR(128)"),
            ("etag", "VARCHAR(256)"),
            ("last_modified", "VARCHAR(256)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
        ),
    ),
    comment="知识库文档定时刷新执行记录",
)

_T_INGESTION_PIPELINE = TableSchema(
    name="t_ingestion_pipeline",
    columns=(
        _pk_col(),
        *_cols(
            ("name", "VARCHAR(100)"),
            ("description", "TEXT"),
            ("created_by", "VARCHAR(64)"),
            ("updated_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="摄取流水线定义表",
)

_T_INGESTION_PIPELINE_NODE = TableSchema(
    name="t_ingestion_pipeline_node",
    columns=(
        _pk_col(),
        *_cols(
            ("pipeline_id", "VARCHAR(32)"),
            ("node_id", "VARCHAR(32)"),
            ("node_type", "VARCHAR(16)"),
            ("next_node_id", "VARCHAR(32)"),
            ("settings_json", "JSONB"),
            ("condition_json", "JSONB"),
            ("created_by", "VARCHAR(64)"),
            ("updated_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="摄取流水线节点连线表（settings/condition 按节点类型存 JSON）",
)

_T_INGESTION_TASK = TableSchema(
    name="t_ingestion_task",
    columns=(
        _pk_col(),
        *_cols(
            ("pipeline_id", "VARCHAR(32)"),
            ("source_type", "VARCHAR(20)"),
            ("source_location", "TEXT"),
            ("source_file_name", "VARCHAR(255)"),
            ("status", "VARCHAR(16)"),
            ("chunk_count", "INTEGER"),
            ("error_message", "TEXT"),
            ("logs_json", "JSONB"),
            ("metadata_json", "JSONB"),
            ("started_at", "TIMESTAMP"),
            ("completed_at", "TIMESTAMP"),
            ("created_by", "VARCHAR(64)"),
            ("updated_by", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="摄取任务实例表",
)

_T_INGESTION_TASK_NODE = TableSchema(
    name="t_ingestion_task_node",
    columns=(
        _pk_col(),
        *_cols(
            ("task_id", "VARCHAR(32)"),
            ("pipeline_id", "VARCHAR(32)"),
            ("node_id", "VARCHAR(32)"),
            ("node_type", "VARCHAR(16)"),
            ("node_order", "INTEGER"),
            ("status", "VARCHAR(16)"),
            ("duration_ms", "BIGINT"),
            ("message", "TEXT"),
            ("error_message", "TEXT"),
            ("output_json", "TEXT"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="摄取任务节点运行记录（NodeLog 落库）",
)

# ==================== P7 U1：用户域 t_user（对齐 Java UserDO） ====================

_T_USER = TableSchema(
    name="t_user",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("username", "VARCHAR(64)"),
            ("password", "VARCHAR(255)"),
            ("avatar", "VARCHAR(500)"),
            ("role", "VARCHAR(16)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="用户",
)


# ==================== P7 A1：审计日志域 t_biz_change_log（对齐 Java BizChangeLogDO） ====================

_T_BIZ_CHANGE_LOG = TableSchema(
    name="t_biz_change_log",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("biz_type", "VARCHAR(64)"),
            ("biz_id", "VARCHAR(64)"),
            ("operation_type", "VARCHAR(32)"),
            ("action_desc", "VARCHAR(500)"),
            # JSONB 快照列：Python 侧以 TEXT 承载 JSON 串（见 tika-porting 决策，InMemory 只登记列名）
            ("before_snapshot", "TEXT"),
            ("after_snapshot", "TEXT"),
            ("change_diff", "TEXT"),
            ("operator_id", "VARCHAR(64)"),
            ("operator_name", "VARCHAR(64)"),
            ("operator_role", "VARCHAR(16)"),
            ("success", "INTEGER"),
            ("error_message", "TEXT"),
            ("class_name", "VARCHAR(128)"),
            ("method_name", "VARCHAR(128)"),
            ("ip", "VARCHAR(64)"),
            ("user_agent", "VARCHAR(500)"),
            ("create_time", "TIMESTAMP"),
        ),
    ),
    comment="业务变更审计日志",
)


# ==================== P0 v1.1：Agent 执行架构域 3 张新表（对齐 ragent-new v2.0.0 260812_agent_engine.sql） ====================
# 与 rag 的 t_conversation / t_message 系列两套分立（ragent.engine.type=agent 时启用）；
# 列类型沿用本项目约定（VARCHAR(32) 主键 / INTEGER 软删），ragent-new 侧的 VARCHAR(20)/SMALLINT 不照搬；
# DDL 边界不支持索引/NOT NULL/DEFAULT：uk_agent_conversation_user 部分唯一索引等由业务侧（dao 层）保证

_T_AGENT_CONVERSATION = TableSchema(
    name="t_agent_conversation",
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
    comment="Agent 会话列表（与 t_conversation 两套分立；逻辑删的旧行不占用唯一键）",
)

_T_AGENT_MESSAGE = TableSchema(
    name="t_agent_message",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("conversation_id", "VARCHAR(64)"),
            ("user_id", "VARCHAR(64)"),
            ("role", "VARCHAR(16)"),
            ("content", "TEXT"),
            ("thinking_content", "TEXT"),
            ("blocks", "JSONB"),
            ("reply_to_message_id", "VARCHAR(32)"),
            ("message_status", "VARCHAR(16)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    comment="Agent 消息记录（终答双写落这里，无来源无角标；blocks 为 reasoning/answer/tool 有序轨迹块，回放时间线唯一来源）",
)

_T_AGENT_STATE = TableSchema(
    name="t_agent_state",
    columns=(
        ColumnSpec(name="user_id", data_type="VARCHAR(64)", primary_key=True),
        ColumnSpec(name="session_id", data_type="VARCHAR(64)", primary_key=True),
        ColumnSpec(name="state_key", data_type="VARCHAR(64)", primary_key=True),
        *_cols(
            ("payload", "JSONB"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
        ),
    ),
    comment="AgentScope 工作状态存储（payload 为框架自有编码的不透明 JSON，业务侧不解析；匿名会话 user_id 为 __anon__）",
)


# 当前消费方用到的全部 t_* 表（4.1 KB provider / 5.1 记忆 store / 摘要 / 5.5 AgentPromptResolver / ChunkMetadataResolver
# / P4 会话/反馈/示例问题/追踪持久化 / P5 摄取与调度域 / v1.1 Agent 执行架构域）
# ==================== P9 阶段 1（§3.2–3.4）：版本化域三张新表 ====================

_T_KNOWLEDGE_REVISION = TableSchema(
    name="t_knowledge_revision",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            # revision_id 即主键（雪花 id，§3.2 不可变版本身份）
            ("document_id", "VARCHAR(32)"),
            ("kb_id", "VARCHAR(32)"),
            ("source_type", "VARCHAR(16)"),
            ("source_hash", "VARCHAR(64)"),
            ("build_fingerprint", "VARCHAR(64)"),
            # BUILT_AWAITING_CONFIRMATION / SUCCEEDED / FAILED / REJECTED / REVOKED
            # （发布状态由 Document.active_revision_id 指针表达，revision 不设 PUBLISHED 态）
            ("status", "VARCHAR(32)"),
            ("committed_attempt_id", "VARCHAR(32)"),
            ("embedding_model", "VARCHAR(64)"),
            ("embedding_dimension", "INTEGER"),
            ("created_at", "TIMESTAMP"),
            ("completed_at", "TIMESTAMP"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    indexes=(
        IndexSpec(
            name="idx_revision_doc_fingerprint",
            columns=("document_id", "build_fingerprint"),
        ),
        IndexSpec(name="idx_revision_doc_status", columns=("document_id", "status")),
    ),
    comment="知识文档不可变版本（§3.2；发布后不可修改，失败重试只换 build_attempt_id）",
)

_T_KNOWLEDGE_UPDATE_JOB = TableSchema(
    name="t_knowledge_update_job",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            # job_id 即主键（雪花 id，§3.3）；build_attempt_id 一次具体执行，重试递增
            ("document_id", "VARCHAR(32)"),
            ("kb_id", "VARCHAR(32)"),
            ("revision_id", "VARCHAR(32)"),
            ("build_attempt_id", "VARCHAR(32)"),
            ("content_hash", "VARCHAR(64)"),
            ("build_fingerprint", "VARCHAR(64)"),
            # QUEUED / RUNNING / BUILT_AWAITING_CONFIRMATION / SUCCEEDED /
            # FAILED_RETRYABLE / FAILED_MANUAL / CANCELED
            ("status", "VARCHAR(32)"),
            ("expected_child_count", "INTEGER"),
            ("expected_parent_count", "INTEGER"),
            ("lease_owner", "VARCHAR(64)"),
            ("lease_token", "VARCHAR(64)"),
            ("lease_until", "TIMESTAMP"),
            ("retry_count", "INTEGER"),
            ("next_retry_at", "TIMESTAMP"),
            ("error_code", "VARCHAR(64)"),
            ("error_message", "TEXT"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
            ("deleted", "INTEGER"),
        ),
    ),
    indexes=(
        # §17.4.5：同文档活跃任务唯一（应用层把唯一冲突翻译为「返回已有 job_id」）
        IndexSpec(
            name="uq_update_job_active_doc",
            columns=("document_id",),
            unique=True,
            where="status IN ('QUEUED', 'RUNNING', 'BUILT_AWAITING_CONFIRMATION')",
        ),
        # 队列扫描：可重试任务领取
        IndexSpec(name="idx_update_job_queue", columns=("status", "next_retry_at")),
    ),
    comment="知识文档版本化更新任务（§3.3；持久化、租约 fencing、重试退避）",
)

_T_KNOWLEDGE_CHUNK_IDENTITY = TableSchema(
    name="t_knowledge_chunk_identity",
    columns=(
        # chunk_key_id = sha256(canonical_natural_key)[:20]（§3.4；截断碰撞由主键唯一性捕获）
        ColumnSpec(name="chunk_key_id", data_type="VARCHAR(20)", primary_key=True),
        *_cols(
            ("document_id", "VARCHAR(32)"),
            ("revision_id", "VARCHAR(32)"),
            ("build_attempt_id", "VARCHAR(32)"),
            ("chunk_index", "INTEGER"),
            ("chunk_role", "VARCHAR(20)"),
            # 指向物理 chunk 行（父子回填 / 引用解析 / 计数对账 / 精确清理的硬依赖）
            ("chunk_id", "VARCHAR(32)"),
            ("parent_key_id", "VARCHAR(20)"),
            # §17.4.4：chunk 内容级 hash 预埋（二期 chunk 级向量复用依赖，现在加列成本为零）
            ("content_hash", "VARCHAR(64)"),
            ("create_time", "TIMESTAMP"),
            ("update_time", "TIMESTAMP"),
        ),
    ),
    indexes=(
        # §3.4：四元组唯一约束——同时捕获截断 hash 碰撞（发现碰撞必须失败，不静默覆盖）。
        # 注意 chunk_index 在 (document_id, revision_id, build_attempt_id) 域内全局唯一，
        # parent/child 共用同一 index 序列，写侧（阶段 3）负责编号。
        IndexSpec(
            name="uq_chunk_identity_natural_key",
            columns=("document_id", "revision_id", "build_attempt_id", "chunk_index"),
            unique=True,
        ),
    ),
    comment="chunk 自然键注册表（§3.4；完整自然键入表，截断 hash 碰撞必须失败）",
)

# P10.1：LLM 调用明细（D4：一行 = 一次真实 API 调用，含 transient 重试与 TTFT 切换；
# append-only 不可变，无 deleted/update_time——保留期由 P10.5 sweeper 按 create_time
# 物理删除；request_uid 为逻辑调用分组键，空 trace 请求级指标的 SQL 归组依据；
# rerank 短路不调 API 不落行）
_T_LLM_CALL = TableSchema(
    name="t_llm_call",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        *_cols(
            ("call_kind", "VARCHAR(20)"),
            ("scene", "VARCHAR(20)"),
            ("purpose", "VARCHAR(40)"),
            ("tier", "VARCHAR(20)"),
            ("provider", "VARCHAR(64)"),
            ("model", "VARCHAR(128)"),
            ("trace_id", "VARCHAR(32)"),
            ("user_id", "VARCHAR(64)"),
            ("request_uid", "VARCHAR(36)"),
            ("attempt_no", "INTEGER"),
            ("is_final", "INTEGER"),
            ("status", "VARCHAR(20)"),
            ("error_type", "VARCHAR(40)"),
            ("prompt_tokens", "INTEGER"),
            ("completion_tokens", "INTEGER"),
            ("token_source", "VARCHAR(20)"),
            ("latency_ms", "INTEGER"),
            ("ttft_ms", "INTEGER"),
            ("cost_usd", "NUMERIC(12,6)"),
            ("pricing_snapshot", "JSONB"),
            ("ext", "JSONB"),
            ("create_time", "TIMESTAMP"),
        ),
    ),
    indexes=(
        IndexSpec(name="idx_llm_call_create_time", columns=("create_time",)),
        IndexSpec(
            name="idx_llm_call_scene_kind",
            columns=("scene", "call_kind", "create_time"),
        ),
        # partial：kb_build 高峰期大量空 trace_id 行不进索引（T1.1 任务分解口径）
        IndexSpec(
            name="idx_llm_call_trace",
            columns=("trace_id",),
            where="trace_id IS NOT NULL",
        ),
    ),
    comment="LLM 调用明细（P10.1/D4：append-only；request_uid = 逻辑调用分组键）",
)

DEFAULT_TABLES: List[TableSchema] = [
    _T_CONVERSATION,
    _T_MESSAGE,
    _T_CONVERSATION_SUMMARY,
    _T_KNOWLEDGE_BASE,
    _T_KNOWLEDGE_CHUNK,
    _T_KNOWLEDGE_DOCUMENT,
    _T_KNOWLEDGE_DOCUMENT_CHUNK_LOG,
    _T_KNOWLEDGE_REVISION,
    _T_KNOWLEDGE_UPDATE_JOB,
    _T_KNOWLEDGE_CHUNK_IDENTITY,
    _T_KNOWLEDGE_DOCUMENT_SCHEDULE,
    _T_KNOWLEDGE_DOCUMENT_SCHEDULE_EXEC,
    _T_INGESTION_PIPELINE,
    _T_INGESTION_PIPELINE_NODE,
    _T_INGESTION_TASK,
    _T_INGESTION_TASK_NODE,
    _T_AGENT_PROFILE,
    _T_AGENT_PROMPT,
    _T_AGENT_PROMPT_VERSION,
    _T_INTENT_NODE,
    _T_QUERY_TERM_MAPPING,
    _T_MESSAGE_FEEDBACK,
    _T_SAMPLE_QUESTION,
    _T_RAG_TRACE_RUN,
    _T_RAG_TRACE_NODE,
    _T_USER,
    _T_BIZ_CHANGE_LOG,
    _T_AGENT_CONVERSATION,
    _T_AGENT_MESSAGE,
    _T_AGENT_STATE,
    _T_LLM_CALL,
]
