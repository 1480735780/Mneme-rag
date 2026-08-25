"""
装配骨架：AppContainer 双 profile（对应 ragent bootstrap Spring 装配）

M0 仅含配置与健康检查依赖（DatabaseClient + CacheManager）：
    - build_memory_stack()：全内存栈（InMemoryDatabaseClient + MemoryCacheManager），测试/演示；
    - build_real_stack()：DB/Redis 栈（SqlDatabaseClient + RedisCacheManager），env 驱动；
后续里程碑（M1 dao / M2 service / M3 chat）在此容器上逐层填充。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.RagentApplication（Spring 启动装配）
    - rag/config/*（线程池/限流等 @Configuration）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config import AppSettings
from rag.dao.agent_dao import AgentPromptDao, AgentProfileDao
from rag.dao.conversation_dao import ConversationDao
from rag.dao.feedback_dao import MessageFeedbackDao
from rag.dao.intent_node_dao import IntentNodeAdminDao
from rag.dao.message_dao import MessageDao
from rag.dao.sample_question_dao import SampleQuestionDao
from rag.dao.summary_dao import ConversationSummaryDao
from rag.dao.trace_dao import RagTraceNodeDao, RagTraceRunDao
from rag.dao.term_mapping_dao import QueryTermMappingAdminDao
from rag.graph.service import GraphQueryService
from rag.intent.tree import RedisIntentTreeCacheManager
from rag.memory.config import MemoryProperties
from rag.prompt.agent_resolver import DatabaseAgentPromptResolver, RedisAgentPromptCacheManager
from rag.prompt.builder import OrchestrationMode
from rag.prompt.formatter import PromptTemplateLoader
from rag.service.conversation_service import (
    ConversationService,
    ConversationTitleGenerator,
)
from rag.service.feedback_service import MessageFeedbackService
from rag.service.message_service import ConversationMessageService
from rag.service.agent_profile_admin_service import AgentProfileAdminService
from rag.service.intent_tree_admin_service import IntentTreeAdminService
from rag.service.recommended_question_service import (
    RecommendedQuestionGenerator,
    RecommendedQuestionService,
)
from rag.service.sample_question_service import SampleQuestionService
from rag.service.settings_service import SystemSettingsService
from rag.service.ratelimit.config import RateLimitProperties
from rag.service.trace_service import RagTraceQueryService, RagTraceRecordService
from rag.service.query_term_mapping_admin_service import QueryTermMappingAdminService
from rag.rewrite.query_rewrite import (
    QueryTermMappingCacheManager,
    RedisQueryTermMappingCacheManager,
)
from storage.cache import CacheManager, MemoryCacheManager, RedisCacheManager
from storage.database import (
    DEFAULT_TABLES,
    DatabaseClient,
    InMemoryDatabaseClient,
    SqlAlchemySqlExecutor,
    SqlDatabaseClient,
)

logger = logging.getLogger(__name__)

# 跨域共享实例懒加载哨兵（对齐 Java 单例 bean：LLM/embedding/vector_store 全容器只建一份）
_MISSING = object()

# AI 模型配置文件（LLM 路由栈装配；与 core/llm/config/ai.yaml 对齐）
_AI_CONFIG_YAML = str(Path(__file__).resolve().parent.parent / "core" / "llm" / "config" / "ai.yaml")


def _load_ai_config() -> Any:
    """加载 AI 模型配置（ai.yaml）；缺失/畸形返回 None（聊天链路与 settings ai 区块随之为空）"""
    try:
        from core.llm.config.config import load_config_from_yaml

        return load_config_from_yaml(_AI_CONFIG_YAML)
    except Exception as ex:  # noqa: BLE001
        logger.warning("AI 模型配置加载失败（settings ai / 聊天链路不装配）: %s", ex)
        return None


def _build_chat_clients(config: Any) -> list:
    """按 config.providers 实例化 chat 客户端（仅支持已知 chat client 的 provider）。

    缺 API key 的 provider 跳过（客户端 requires_api_key 时调用会失败，不应进入候选）；
    Ollama 无需 API key（ai.yaml api_key 为空）→ 单独放行；其余 provider 未配 key 时跳过。
    """
    from core.llm.providers.aihubmix import AIHubMixChatClient
    from core.llm.providers.ollama import OllamaChatClient
    from core.llm.providers.openai import OpenAIChatClient
    from core.llm.providers.qwen import QwenChatClient
    from core.llm.providers.siliconflow import SiliconFlowChatClient

    factory = {
        "qwen": QwenChatClient,
        "openai": OpenAIChatClient,
        "ollama": OllamaChatClient,
        "siliconflow": SiliconFlowChatClient,
        "aihubmix": AIHubMixChatClient,
    }
    clients: list = []
    for name, provider in config.providers.items():
        client_cls = factory.get(name)
        if client_cls is None:
            logger.warning("provider %s 暂无 chat client 实现，跳过", name)
            continue
        api_key = str(getattr(provider, "api_key", "") or "").strip()
        if name != "ollama" and (not api_key or api_key.startswith("${")):
            # 占位符未解析（如 ${QWEN_API_KEY} 未设环境变量）→ 视为无 key，不进入候选
            logger.info("provider %s 未配置有效 api_key（占位符未解析），跳过", name)
            continue
        try:
            clients.append(client_cls())
        except Exception as ex:  # noqa: BLE001 —— 单 provider 构建失败不阻断其余
            logger.warning("provider %s chat client 构建失败: %s", name, ex)
    return clients


def _build_database(settings: AppSettings) -> SqlDatabaseClient:
    """按 RAGENT_DATABASE_URL 装配关系库；未配置回落 sqlite 内存库兜底（对齐 M0 语义）

    P6 0.1（决策 D2 env 驱动、逐项独立兜底）：显式配了连接串走真实后端（PG/MySQL，
    pool_pre_ping 防断连），未配回落 sqlite 内存库——保证 real 栈不设 env 时行为与现状完全一致。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    url = (settings.database_url or "").strip()
    if url:
        engine = create_engine(url, pool_pre_ping=True)
    else:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    db = SqlDatabaseClient(SqlAlchemySqlExecutor(engine=engine))
    db.ensure_schema(DEFAULT_TABLES)
    return db


def _build_cache(settings: AppSettings):
    """按 RAGENT_REDIS_URL 装配缓存：非空 → RedisCacheManager + redis.asyncio 客户端（挂 container.redis）；空 → Memory 兜底

    P6 0.1（决策 D2）：redis 客户端随 container.redis 注入，供 _build_rate_limiter 的
    rate_limit_backend=redis 分支使用（RedisFairRateLimiter）；未配置回落 Memory。
    """
    url = (settings.redis_url or "").strip()
    if not url:
        return MemoryCacheManager(), None
    import redis.asyncio as aioredis

    client = aioredis.from_url(url)
    return RedisCacheManager(client), client


def build_parser_registry(file_storage, vlm_service=None):
    """构造 ParserRegistry：Csv/Excel/Image 全量注册；MinerU 仅在配置了 RAGENT_MINERU_API_KEY 时条件注册。

    对齐 youcom 工具条件注册先例：无 key 时 PDF/DOC/PPT 保持现状（上传前校验拒绝）；
    Csv/Excel/Image 无需外部服务，始终注册（含无 key 环境）。
    """
    from rag.ingestion.parser.csv_parser import CsvDocumentParser
    from rag.ingestion.parser.excel.excel_parser import ExcelDocumentParser
    from rag.ingestion.parser.image_parser import ImageDocumentParser, ImageParseProperties
    from rag.ingestion.parser.markdown_parser import MarkdownDocumentParser
    from rag.ingestion.parser.mineru.client import MinerUClient
    from rag.ingestion.parser.mineru.parser import MinerUDocumentParser
    from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor
    from rag.ingestion.parser.mineru.properties import MinerUProperties
    from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker
    from rag.ingestion.parser.registry import ParserRegistry
    from rag.ingestion.parser.text_parser import TextDocumentParser

    parsers = [
        TextDocumentParser(),
        MarkdownDocumentParser(),
        CsvDocumentParser(),
        ExcelDocumentParser(),
        ImageDocumentParser(vlm_service, file_storage, ImageParseProperties()),
    ]
    mineru_props = MinerUProperties.from_env()
    if mineru_props.api_key:
        client = MinerUClient(mineru_props)
        polling = MinerUPollingExecutor(client, mineru_props)
        unpacker = MinerUResultUnpacker(file_storage, vlm_service, ImageParseProperties())
        parsers.append(MinerUDocumentParser(client, polling, unpacker, mineru_props))
    return ParserRegistry(parsers)


@dataclass
class AppContainer:
    """
    应用装配容器（对应 Java Spring 容器，P4 手动装配版）

    Attributes:
        settings: 运行配置
        db:       DatabaseClient（M1 dao 层依赖）
        cache:    CacheManager（M3 幂等/限流等依赖）
        engine:   RAGChatEngine（M3 填充，M0 为 None）
    """

    settings: AppSettings
    db: DatabaseClient
    cache: CacheManager
    # M6 限流 Redis 后端（rate_limit_backend=redis 时注入 redis.asyncio.Redis；缺省 None = process 后端）
    redis: Any = None
    # M7 引擎全链装配：memory/llm 注入槽（测试注入桩；生产由 _wire_memory/_build_llm 构建）
    memory_service: Any = None
    llm_service: Any = None
    # 向量化注入槽：写侧（knowledge 域 chunk 落库）与读侧（检索 InMemoryVectorStore）共享同一实例，
    # 无真实 key 环境经槽注入桩；生产由 _build_embedding_service 按 ai.yaml 构建
    embedding_service: Any = None
    # AI 模型配置（ai.yaml 单次加载；settings ai 区块与 _build_llm 共享同一实例）
    ai_config: Any = None
    # 检索通道后端注入槽（快赢①：生产由 _build_retrieval_engine 按 RetrievalProperties 构建真实客户端；
    # 测试可注入桩客户端/属性经槽位进入引擎）
    retrieval_properties: Any = None
    web_search_client: Any = None
    light_rag_client: Any = None
    keyword_retriever: Any = None
    vector_retriever: Any = None
    # 有效知识库 collection 提供者（检索作用域用）：生产默认 DatabaseKbCollectionProvider(db)；
    # 测试注入 StaticKbCollectionProvider（P5 知识库域落地前全库范围为空）
    kb_collection_provider: Any = None
    # 缓存容器级共享（5.3/5.5 实例同一性：admin 写后清缓存与读路径必须指向同一存储）
    intent_tree_cache: Any = None
    agent_prompt_cache: Any = None
    conversation_service: Optional[ConversationService] = None
    message_service: Optional[ConversationMessageService] = None
    feedback_service: Optional[MessageFeedbackService] = None
    recommended_question_service: Optional[RecommendedQuestionService] = None
    sample_question_service: Optional[SampleQuestionService] = None
    trace_record_service: Optional[RagTraceRecordService] = None
    trace_query_service: Optional[RagTraceQueryService] = None
    query_term_mapping_admin_service: Optional[QueryTermMappingAdminService] = None
    # 术语映射缓存**单一共享实例**：admin 写后清缓存与读路径（DatabaseQueryTermMappingService，M7 装配）
    # 必须指向同一存储——否则内存 profile 下各实例私有 dict 本地失效即为 no-op（读路径永远旧快照）
    query_term_mapping_cache: Optional[QueryTermMappingCacheManager] = None
    intent_tree_admin_service: Optional[IntentTreeAdminService] = None
    agent_profile_admin_service: Optional[AgentProfileAdminService] = None
    settings_service: Optional[SystemSettingsService] = None
    graph_service: Optional[GraphQueryService] = None
    rate_limit_properties: Optional[RateLimitProperties] = None
    engine: Any = None
    stream_task_manager: Any = None
    idempotent_guard: Any = None
    chat_service: Any = None
    # P5 knowledge 域（N1-N3：KB/文档/分块 service + 摄取配置 schema 提供器；N4 调度 / N5 流水线后续接入）
    knowledge_base_service: Any = None
    knowledge_document_service: Any = None
    knowledge_chunk_service: Any = None
    ingestion_spec_schema_provider: Any = None
    # P5 N4 调度域：登记服务 + 调度任务（lifespan 挂载 start/stop 协程）
    knowledge_schedule_service: Any = None
    knowledge_schedule_job: Any = None
    # P5 N5 摄取流水线域：pipeline/task 服务（intent_tree 复用 M5 IntentTreeAdminService）
    ingestion_pipeline_service: Any = None
    ingestion_task_service: Any = None
    # P7 认证域：用户 DAO + 会话管理 + 认证服务（AuthController 依赖）
    user_dao: Any = None
    session_manager: Any = None
    auth_service: Any = None
    user_service: Any = None
    # P7 审计域：日志查询服务（BizChangeLogController 依赖）
    change_log_query_service: Any = None
    # P7 D 组大盘域：聚合服务（DashboardController 依赖）
    dashboard_service: Any = None
    # P8 E 组评测域：检索评测服务（EvalController 依赖；引擎就绪才装配，否则 None）
    eval_service: Any = None
    # P1 Agent MVP：Agent 门面 + MCP 注册表注入槽（AgentController 依赖；引擎就绪才装配，否则 None）
    agent_service: Any = None
    mcp_tool_registry: Any = None
    _mcp_autoconfig: Any = None
    _owned: list = field(default_factory=list)

    # ==================== 双 profile 装配 ====================

    @classmethod
    def build(cls, settings: Optional[AppSettings] = None) -> "AppContainer":
        """按配置选择装配栈（对齐 Java @ConditionalOnProperty 语义）"""
        settings = settings or AppSettings.from_env()
        if settings.is_memory():
            container = cls._build_memory(settings)
        else:
            container = cls._build_real(settings)
        cls._ensure_init_admin(container, settings)
        return container

    @classmethod
    def _ensure_init_admin(cls, container: "AppContainer", settings: AppSettings) -> None:
        """P2 部署资源：RAGENT_INIT_ADMIN_USERNAME/PASSWORD 齐备时确保管理员存在（幂等）

        两个 env 均非空才播种；已存在同名用户则跳过。默认（env 未设置）无任何行为变化。
        """
        username = (settings.init_admin_username or "").strip()
        password = settings.init_admin_password or ""
        if not username or not password:
            return
        dao = getattr(container, "user_dao", None)
        if dao is None or dao.find_by_username(username) is not None:
            return
        from rag.dao.support import NOT_DELETED
        from user.enums import UserRole
        from user.service.password import hash_password

        dao.insert(
            {
                "id": f"init-{username}",
                "username": username,
                "password": hash_password(password),
                "avatar": "",
                "role": UserRole.ADMIN.value,
                "deleted": NOT_DELETED,
            }
        )
        logging.getLogger(__name__).info("已播种初始管理员: %s", username)

    @classmethod
    def _build_memory(cls, settings: AppSettings) -> "AppContainer":
        """全内存栈（InMemory DB + Memory 缓存），测试/演示 profile"""
        db = InMemoryDatabaseClient()
        db.ensure_schema(DEFAULT_TABLES)
        container = cls(settings=settings, db=db, cache=MemoryCacheManager())
        container._wire_conversation_services()
        container._wire_ingestion_services()
        container._wire_knowledge_services()
        container._wire_chat_services()
        container._wire_eval_services()
        container._wire_agent_services()   # P1 Agent MVP：须在 _wire_chat_services 之后（engine 已装配）
        container._wire_idempotent_framework()
        container._wire_auth_services()
        container._wire_audit_services()
        container._wire_dashboard_services()
        return container

    @classmethod
    def _build_real(cls, settings: AppSettings) -> "AppContainer":
        """真实栈（P6 0.1 逐项装配：DB / Redis 按 env 注入，缺省逐项回落 sqlite / Memory）

        - 关系库：RAGENT_DATABASE_URL 非空 → 该连接串（PG/MySQL）；空 → sqlite 内存库兜底；
        - 缓存：  RAGENT_REDIS_URL 非空 → RedisCacheManager + container.redis（供 RedisFairRateLimiter）；
                  空 → Memory 兜底；
        - 向量 / 对象存储：经 _build_vector_store / _build_object_storage 分派
          （向量：memory 兜底 / milvus 1.1 / pgvector 1.2；对象存储：memory 兜底 / s3 2.1 / oss 2.2 可选）。
        LLM 路由（标题生成）M3 注入前回退默认标题。
        """
        db = _build_database(settings)
        cache, redis_client = _build_cache(settings)
        container = cls(settings=settings, db=db, cache=cache, redis=redis_client)
        container._wire_conversation_services()
        container._wire_ingestion_services()
        container._wire_knowledge_services()
        container._wire_chat_services()
        container._wire_eval_services()
        container._wire_agent_services()   # P1 Agent MVP：须在 _wire_chat_services 之后（engine 已装配）
        container._wire_idempotent_framework()
        container._wire_auth_services()
        container._wire_audit_services()
        container._wire_dashboard_services()
        return container

    # ==================== service 装配 ====================

    def _wire_conversation_services(self) -> None:
        """组装会话/消息 service（M2 会话域）——dao 注入、标题生成 LLM M3 注入（缺省回退默认标题）"""
        conversation_dao = ConversationDao(self.db)
        message_dao = MessageDao(self.db)
        summary_dao = ConversationSummaryDao(self.db)
        feedback_dao = MessageFeedbackDao(self.db)
        title_generator = ConversationTitleGenerator(
            llm_service=None,  # M3 注入真实 LLM 路由；缺省回退「新对话」
            template_loader=PromptTemplateLoader(),
            properties=MemoryProperties(),
        )
        self.conversation_service = ConversationService(
            conversation_dao=conversation_dao,
            message_dao=message_dao,
            summary_dao=summary_dao,
            title_generator=title_generator,
        )
        self.message_service = ConversationMessageService(
            conversation_dao=conversation_dao,
            message_dao=message_dao,
            feedback_dao=feedback_dao,
        )
        # M4 反馈与推荐追问域（4.4/4.5 在线服务）：复用已建 dao，随机列表/管理端点常驻可用
        self.feedback_service = MessageFeedbackService(feedback_dao, message_dao)
        self.recommended_question_service = RecommendedQuestionService(
            message_dao,
            RecommendedQuestionGenerator(),  # llm 由 M7 engine 装配注入；缺省 None 时生成降级 FAILED
        )
        self.sample_question_service = SampleQuestionService(SampleQuestionDao(self.db))
        # M5 管理与治理域（5.1/5.2 追踪）：run/节点记录 + 后台查询，hosting trace_runner（M3）
        run_dao = RagTraceRunDao(self.db)
        node_dao = RagTraceNodeDao(self.db)
        self.trace_record_service = RagTraceRecordService(run_dao, node_dao)
        self.trace_query_service = RagTraceQueryService(run_dao, node_dao)
        # M5 5.3 术语映射管理（写后清缓存；读路径仍由 rewrite.query_rewrite 承载）
        # 注意：缓存用容器**单一共享实例**，M7 装配 DatabaseQueryTermMappingService 时必须复用
        #   container.query_term_mapping_cache，否则内存 profile 下 clear 本地私有 dict 为 no-op
        self.query_term_mapping_cache = RedisQueryTermMappingCacheManager(cache_manager=self.cache)
        self.query_term_mapping_admin_service = QueryTermMappingAdminService(
            QueryTermMappingAdminDao(self.db),
            cache_manager=self.query_term_mapping_cache,
        )
        # M5 5.4 意图树管理（写后清 intent 树缓存；容器级共享，engine 意图分类器复用同一实例）
        self.intent_tree_cache = RedisIntentTreeCacheManager(cache_manager=self.cache)
        self.intent_tree_admin_service = IntentTreeAdminService(
            IntentNodeAdminDao(self.db),
            cache_manager=self.intent_tree_cache,
        )
        # M5 5.5 Agent 档案管理（提示词读路径共享同一缓存实例；engine 提示词解析复用同一实例）
        self.agent_prompt_cache = RedisAgentPromptCacheManager(cache_manager=self.cache)
        orchestration_mode = OrchestrationMode.of(self.settings.orchestration_mode)
        self.agent_profile_admin_service = AgentProfileAdminService(
            profile_dao=AgentProfileDao(self.db),
            prompt_dao=AgentPromptDao(self.db),
            resolver=DatabaseAgentPromptResolver(self.db, cache_manager=self.agent_prompt_cache),
            prompt_cache_manager=self.agent_prompt_cache,
            mode=orchestration_mode,  # 从 AppSettings 回注，槽位生效集随编排模式
        )
        # M5 5.6 设置聚合：ai 模型配置单次加载注入（settings ai 区块 + _build_llm 共享）
        self.ai_config = _load_ai_config()
        self.rate_limit_properties = RateLimitProperties.from_env()
        self.settings_service = SystemSettingsService(
            memory_properties=MemoryProperties(),
            ai_config=self.ai_config,  # ai 区块：providers 脱敏 + 模型组 + 熔断 + 流式
            query_rewrite_enabled=True,
            citation_enabled=False,
            orchestration_mode=orchestration_mode.value,  # 与 5.5 槽位生效集同源
            rate_limit=self.rate_limit_properties,  # 单真源：直接收 RateLimitProperties
        )
        # M5 5.7 图谱可视化（C12，委托既有 GraphQueryService）
        self.graph_service = GraphQueryService()

    def _wire_auth_services(self) -> None:
        """组装 P7 认证域：用户 DAO + 会话管理 + 认证服务（AuthController 依赖）

        - 会话存储复用容器 cache（内存兜底 / Redis 一致，D1/D2 决策）
        - user_dao 面向容器 db（t_user 已入 DEFAULT_TABLES）
        """
        from user.dao.user_dao import UserDao
        from user.service.auth_service import AuthService
        from user.service.session_manager import SessionManager
        from user.service.user_service import UserService

        self.user_dao = UserDao(self.db)
        self.session_manager = SessionManager(cache=self.cache)
        self.auth_service = AuthService(self.user_dao, self.session_manager)
        self.user_service = UserService(self.user_dao)

    def _wire_audit_services(self) -> None:
        """组装 P7 审计域：日志查询服务 + 落库服务（BizChangeLogController / @record_biz_change 消费）"""
        from audit.dao.change_log_dao import BizChangeLogDao
        from audit.service.change_log_query_service import BizChangeLogQueryService
        from audit.service.record_service import BizChangeLogRecordService
        from audit.support.decorator import set_record_service

        change_log_dao = BizChangeLogDao(self.db)
        self.change_log_query_service = BizChangeLogQueryService(change_log_dao)
        # 业务变更审计落库服务（A5 起 @record_biz_change 装饰器消费，与查询服务共享同一 DAO）
        set_record_service(BizChangeLogRecordService(dao=change_log_dao))

    def _wire_dashboard_services(self) -> None:
        """组装 P7 D 组大盘服务：DashboardService（DashboardController 依赖）

        面向容器 db 聚合 user/conversation/message/trace_run 四表统计（InMemory / Sql 无感知）。
        """
        from admin.service.dashboard_service import DashboardService

        self.dashboard_service = DashboardService(self.db)

    def _wire_eval_services(self) -> None:
        """P8 E 组：评测检索服务（EvalController 依赖）

        从装配好的引擎提取 rewrite/intent/retrieval 组件（D9 前置：评测环境须 LLM 就绪 +
        检索通道启用——引擎未就绪（无 LLM）时 eval_service 保持 None，端点不挂载）。
        须在 _wire_chat_services 之后调用（engine 在其中装配）。
        """
        if self.engine is None:
            return
        from knowledge.dao.chunk import KnowledgeChunkDao
        from rag.service.eval_service import EvalRetrievalService

        self.eval_service = EvalRetrievalService(
            query_rewrite_service=self.engine._query_rewrite_service,
            intent_resolver=self.engine._intent_resolver,
            retrieval_engine=self.engine._retrieval_engine,
            budget=self.engine._budget,
            scope_resolver=self.engine._scope_resolver,
            chunk_dao=KnowledgeChunkDao(self.db),
            db=self.db,
        )

    def _wire_agent_services(self) -> None:
        """P1 Agent MVP：AgentChatService（AgentController 依赖）

        复用 _wire_eval_services 的「引擎组件提取」先例：从 engine 取 retrieval_engine/budget/
        scope_resolver；LLM 用共享路由（_get_shared_llm）。MCP registry 注入槽优先，
        否则 McpClientAutoConfiguration 自动装配（无配置 → 空注册表，仅内置 knowledge_search）。
        引擎/LLM 未就绪 → agent_service=None，端点不挂载（半装配防护）。
        须在 _wire_chat_services 之后调用（engine 在其中装配）。
        """
        llm = self._get_shared_llm()
        if llm is None or self.engine is None:
            return
        from core.pipeline.agent_pipeline import AgentPipeline
        from rag.mcp import McpClientAutoConfiguration, McpClientProperties, DefaultMcpToolRegistry
        from rag.service.agent_service import AgentChatService

        registry = self.mcp_tool_registry  # 注入槽优先（测试/外部装配）
        if registry is None:
            registry = DefaultMcpToolRegistry()
            # P2 部署资源：从 RAGENT_MCP_SERVERS_JSON 解析 MCP Server（空 → 空注册表，仅内置 knowledge_search；
            # 兼容 {"servers":[...]} 与裸数组两种形态；解析失败告警跳过远程工具注册）
            properties = McpClientProperties()
            raw_servers = (self.settings.mcp_servers_json or "").strip()
            if raw_servers:
                import json

                try:
                    parsed = json.loads(raw_servers)
                    if isinstance(parsed, list):  # 裸数组形态 → 包一层
                        parsed = {"servers": parsed}
                    properties = McpClientProperties.from_dict(parsed)
                except (ValueError, TypeError):
                    logger.warning(
                        "RAGENT_MCP_SERVERS_JSON 解析失败，MCP 远程工具跳过注册: %s", raw_servers
                    )
            autoconfig = McpClientAutoConfiguration(properties, registry)
            autoconfig.init()  # servers 为空 → 空注册表；失败 server 跳过
            self._mcp_autoconfig = autoconfig
            self._owned.append(_McpAutoconfigCloser(autoconfig))  # aclose 时 destroy 客户端

        pipeline = AgentPipeline(
            llm,
            tool_registry=registry,
            retrieval_engine=self.engine._retrieval_engine,
            budget=self.engine._budget,
            scope_resolver=self.engine._scope_resolver,
        )
        self.agent_service = AgentChatService(pipeline)

    def _wire_idempotent_framework(self) -> None:
        """P7 F 组：把容器级幂等守卫注入装饰器全局槽（@idempotent_submit 消费）

        复用 chat 域的 IdempotentSubmitGuard（与流式端点同一 cache，内存/Redis 一致），
        需在 _wire_chat_services 之后调用（guard 已创建）。未装配 chat 域时装饰器走内存兜底。
        """
        from common.idempotent.submit import set_guard

        set_guard(self.idempotent_guard)

    def _wire_chat_services(self) -> None:
        """组装流式/聊天依赖（M3 切片 + M6 限流 + M7 引擎全链）：
        任务管理器 + 幂等守卫 + 容器级 memory + 真实 engine（LLM 可用时）→ chat_service"""
        from rag.service.idempotent import IdempotentSubmitGuard
        from rag.service.stream.task_manager import StreamTaskManager

        self.stream_task_manager = StreamTaskManager(cache=self.cache)
        self.idempotent_guard = IdempotentSubmitGuard(cache=self.cache)
        # 容器级记忆：engine 加载历史 + handler 落库 + reject 落库共享同一实例（M7 ②）
        self._wire_memory()
        # LLM：注入槽（测试桩）优先；生产经 _build_llm 按 ai.yaml 装配路由栈（无可用客户端 → None）
        llm = self._get_shared_llm()  # 跨域共享（ingestion/chat 同一 LLM 路由，熔断状态同源）
        if llm is not None:
            # 快赢②：回注推荐追问生成器（此前 `_wire_conversation_services` 先于 engine 装配，generator 缺省 None → 恒 FAILED）
            self.recommended_question_service.inject_llm(llm)
        if llm is not None and self.engine is None:
            self._wire_engine(llm)  # 装配真实引擎 → factory 的 C1 聊天路由随之条件挂载
        if self.engine is not None:
            self._wire_history_chat_service()

    def _wire_memory(self) -> None:
        """构建容器级会话记忆服务（M7 ②：engine / handler / reject 三处共享）"""
        from rag.memory import (
            DatabaseConversationMemoryStore,
            DefaultConversationMemoryService,
            MemoryConversationMemorySummaryService,
        )
        from rag.memory.config import MemoryProperties

        if self.memory_service is not None:
            return  # 已注入（测试/外部装配），不覆盖
        props = MemoryProperties()
        self.memory_service = DefaultConversationMemoryService(
            memory_store=DatabaseConversationMemoryStore(self.db, props),
            summary_service=MemoryConversationMemorySummaryService(properties=props),
        )

    def _wire_engine(self, llm_service: Any) -> None:
        """装配真实 RAGChatEngine（M7 ② C14：改写/意图/引导/检索/Prompt/LLM 全链）"""
        from rag.engine import RAGChatEngine
        from rag.guidance.checker import AmbiguityLLMChecker
        from rag.guidance.config import GuidanceProperties
        from rag.guidance.service import IntentGuidanceService
        from rag.intent.classifier import DefaultIntentClassifier, IntentResolver
        from rag.intent.tree import IntentTreeFactory
        from rag.prompt.builder import RAGPromptService
        from rag.rewrite.query_rewrite import MultiQuestionRewriteService

        # 改写：LLM 开关关闭时走「术语归一化 + 规则拆分」兜底，不依赖真实模型
        query_rewrite = MultiQuestionRewriteService(llm_service, enabled=False)
        # 意图：LLM 分类 + 容器级意图树缓存（admin 写后清缓存与分类器读同源）
        classifier = DefaultIntentClassifier(
            llm_service,
            cache_manager=self.intent_tree_cache,
            tree_loader=IntentTreeFactory.build_intent_tree,  # 静态 demo 树；DB 树由后续接线替换
        )
        intent_resolver = IntentResolver(classifier)
        guidance = IntentGuidanceService(
            GuidanceProperties(),
            classifier,  # 作为 IntentNodeRegistry（parentId 上溯）
            AmbiguityLLMChecker(llm_service),
        )
        # 检索：按 RetrievalProperties（env）装配多通道引擎（快赢①：检索通道按配置展开）
        retrieval = self._build_retrieval_engine()
        # Prompt：引用默认关（与 5.6 settings 同源）；Agent 提示词解析复用 5.5 共享缓存
        prompt = RAGPromptService(
            agent_prompt_resolver=DatabaseAgentPromptResolver(
                self.db, cache_manager=self.agent_prompt_cache
            ),
            citation_enabled=False,
        )
        self.engine = RAGChatEngine(
            query_rewrite_service=query_rewrite,
            intent_resolver=intent_resolver,
            guidance_service=guidance,
            retrieval_engine=retrieval,
            llm_service=llm_service,
            prompt_builder=prompt,
            memory_service=self.memory_service,
            scope_resolver=self._scope_resolver,  # 与检索引擎共用同一实例（引擎显式传该 resolver）
        )

    def _build_retrieval_engine(self) -> Any:
        """按 RetrievalProperties（env）装配多通道检索引擎（快赢①：检索通道按配置展开）

        四通道各自启停由 env 开关驱动；后端客户端优先取注入槽（测试桩），否则按配置构建真实客户端：
            - 向量   vector_enabled   → 注入槽 / InMemoryVectorStore(+embedding 服务，无则跳过)
            - 关键词 keyword_enabled  → 注入槽 / EsKeywordRetrieverService(EsProperties())
            - 图谱   graph_enabled    → 注入槽 / HttpLightRagClient(LightRagProperties(base_url, api_key))
            - 联网   web_enabled      → 注入槽 / YouComWebSearchClient(api_key)（key 不可解析则跳过）
        无任何启用通道 → 保持既有「空检索兜底」形态（三通道禁用）。
        """
        from rag.retrieval.channel.graph_channel import GraphSearchChannel
        from rag.retrieval.channel.keyword_channel import KeywordSearchChannel
        from rag.retrieval.channel.vector_channel import VectorSearchChannel
        from rag.retrieval.channel.web_search_channel import WebSearchChannel
        from rag.retrieval.config import RetrievalProperties
        from rag.retrieval.engine import MultiChannelRetrievalEngine
        from rag.retrieval.postprocessor.dedup import DeduplicationPostProcessor
        from rag.retrieval.postprocessor.fusion import FusionPostProcessor
        from rag.retrieval.postprocessor.metadata_enrichment import MetadataEnrichmentPostProcessor

        props = self.retrieval_properties or RetrievalProperties.from_env()
        # 检索通道配置一致性校验（对齐 Java RetrievalConfigFailureAnalyzer；告警不阻断，保持既有装配行为）
        from rag.retrieval.config_validation import RetrievalConfigException, validate_env

        _violations = validate_env()
        if _violations:
            logger.warning("检索通道配置矛盾（启动不阻断）: %s", RetrievalConfigException(_violations).format_failure())
        channels = []

        # 向量：读侧优先注入槽；生产用 InMemoryVectorStore + ai.yaml embedding 服务（无则跳过，Milvus/Pg 由 P6 接线）
        if props.vector_enabled:
            retriever = self.vector_retriever
            if retriever is None:
                retriever = self._build_memory_vector_retriever()
            if retriever is not None:
                channels.append(VectorSearchChannel(retriever, enabled=True))

        # 关键词：注入槽 / ES 真实后端（未配置 ES 时 EsProperties 默认 localhost:9200，搜索失败降级空）
        if props.keyword_enabled:
            retriever = self.keyword_retriever
            if retriever is None:
                from rag.keyword.config import EsProperties
                from rag.keyword.es import EsKeywordRetrieverService

                retriever = EsKeywordRetrieverService(properties=EsProperties())
            if retriever is not None:
                channels.append(KeywordSearchChannel(retriever, enabled=True))

        # 图谱：注入槽 / HttpLightRagClient（LightRagProperties 由 env 展开）
        if props.graph_enabled:
            client = self.light_rag_client
            if client is None:
                from rag.graph.client import HttpLightRagClient
                from rag.graph.config import LightRagProperties

                client = HttpLightRagClient(
                    LightRagProperties(base_url=props.lightrag_url, api_key=props.lightrag_api_key)
                )
            if client is not None:
                channels.append(GraphSearchChannel(client, enabled=True))

        # 联网：注入槽 / YouComWebSearchClient（api key 不可解析则跳过，与通道 is_enabled 语义一致）
        if props.web_search_enabled:
            from rag.websearch.client import YouComWebSearchClient

            api_key = (props.web_api_key or "").strip()
            client = self.web_search_client or (
                YouComWebSearchClient(api_key=api_key) if api_key else None
            )
            if client is not None:
                channels.append(WebSearchChannel(client, enabled=True, api_key=api_key))

        if not channels:
            logger.info("未启用任何检索通道（RAGENT_RETRIEVAL_* 全 off），引擎空检索兜底")
            channels = [
                KeywordSearchChannel(None, enabled=False),
                GraphSearchChannel(None, enabled=False),
                WebSearchChannel(None, enabled=False),
            ]

        # 检索作用域：按子问题解析一次（定向/全局）；生产用 DB 知识库表，测试可注入 Static 桩
        from rag.retrieval.channel.kb_collection_provider import DatabaseKbCollectionProvider
        from rag.retrieval.channel.scope_resolver import RetrievalScopeResolver
        from rag.retrieval.config import ScopeProperties

        # 引擎级共用同一实例：RAGChatEngine 显式传 self._scope_resolver 覆盖检索引擎内部 resolver
        self._scope_resolver = RetrievalScopeResolver(
            ScopeProperties(),
            self.kb_collection_provider or DatabaseKbCollectionProvider(self.db),
        )
        return MultiChannelRetrievalEngine(
            channels=channels,
            postprocessors=[
                DeduplicationPostProcessor(),
                FusionPostProcessor(),
                MetadataEnrichmentPostProcessor(),
            ],
            scope_resolver=self._scope_resolver,
        )

    def _build_memory_vector_retriever(self) -> Any:
        """构建内存向量读侧：直接复用容器级共享向量库（写读同一单例，Java VectorStore 语义）；
        无可用 embedding 客户端 → None"""
        embedding = self._get_shared_embedding()
        if embedding is None:
            logger.warning("向量通道启用但无可用 embedding 客户端，跳过向量通道")
            return None
        return self._get_shared_vector_store()

    def _build_embedding_service(self, config: Any) -> Any:
        """按 ai.yaml 构建路由式向量化服务（镜像 _build_chat_clients：已知 embedding provider + 缺 key 跳过）"""
        from core.llm.embedding import RoutingEmbeddingService
        from core.llm.model.health_store import ModelHealthStore
        from core.llm.model.selector import ModelSelector
        from core.llm.model.routing_executor import RoutingExecutor
        from core.llm.providers.ollama_embedding import OllamaEmbeddingClient
        from core.llm.providers.openai_embedding import OpenAIEmbeddingClient
        from core.llm.providers.qwen_embedding import QwenEmbeddingClient
        from core.llm.providers.siliconflow_embedding import SiliconFlowEmbeddingClient

        factory = {
            "ollama": OllamaEmbeddingClient,
            "siliconflow": SiliconFlowEmbeddingClient,
            "qwen": QwenEmbeddingClient,
            "openai": OpenAIEmbeddingClient,
        }
        clients: list = []
        for name, provider in config.providers.items():
            client_cls = factory.get(name)
            if client_cls is None:
                continue
            api_key = str(getattr(provider, "api_key", "") or "").strip()
            if name != "ollama" and (not api_key or api_key.startswith("${")):
                # 占位符未解析（如 ${SILICONFLOW_API_KEY} 未设环境变量）→ 视为无 key，不进入候选
                continue
            try:
                clients.append(client_cls())
            except Exception as ex:  # noqa: BLE001 —— 单 provider 构建失败不阻断其余
                logger.warning("provider %s embedding client 构建失败: %s", name, ex)
        if not clients:
            return None
        selection = getattr(config, "selection", None) or {}
        health_store = ModelHealthStore(
            failure_threshold=int(getattr(selection, "failure_threshold", 2) or 2),
            open_duration_ms=int(getattr(selection, "open_duration_ms", 30000) or 30000),
        )
        return RoutingEmbeddingService(ModelSelector(config, health_store), RoutingExecutor(health_store), clients)

    def _build_llm(self) -> Any:
        """生产装配真实 LLM 路由栈（对应 Java LLM @Configuration）：ai.yaml + 熔断 + 按 provider 建 chat client

        无可用 chat client（缺 API key / provider 无客户端实现）→ 返回 None，聊天链路不装配
        （factory 条件挂载随之不暴露 C1，保持半装配防护语义）。
        """
        from core.llm.chat import RoutingLLMService
        from core.llm.model.health_store import ModelHealthStore
        from core.llm.model.selector import ModelSelector
        from core.llm.model.routing_executor import RoutingExecutor

        config = self.ai_config if self.ai_config is not None else _load_ai_config()
        if config is None:
            return None
        clients = _build_chat_clients(config)
        if not clients:
            logger.warning("未装配到可用的 chat 客户端（检查 ai.yaml api_key），聊天链路不装配")
            return None
        selection = getattr(config, "selection", None) or {}
        health_store = ModelHealthStore(
            failure_threshold=int(getattr(selection, "failure_threshold", 2) or 2),
            open_duration_ms=int(getattr(selection, "open_duration_ms", 30000) or 30000),
        )
        selector = ModelSelector(config, health_store)
        executor = RoutingExecutor(health_store)
        return RoutingLLMService(selector, health_store, executor, clients, config)

    # ==================== 跨域共享实例（对齐 Java 单例 bean） ====================

    def _get_shared_llm(self) -> Any:
        """容器级共享 LLM 路由栈：knowledge/chat 与 ingestion 共用，避免熔断状态分裂

        注入槽（self.llm_service，测试桩可后注入）优先且不缓存；否则首次调 _build_llm 并缓存，
        保证全容器同一 LLM 实例（对齐 Java 单例 bean）。
        """
        if self.llm_service is not None:
            return self.llm_service
        cached = getattr(self, "_shared_llm", _MISSING)
        if cached is _MISSING:
            cached = self._build_llm()
            self._shared_llm = cached
        return cached

    def _get_shared_embedding(self) -> Any:
        """容器级共享 embedding 路由（懒建一次）：knowledge 与 ingestion 共用，杜绝重复建客户端"""
        cached = getattr(self, "_shared_embedding", _MISSING)
        if cached is _MISSING:
            cached = self.embedding_service  # 注入槽（测试桩）优先
            if cached is None:
                config = self.ai_config if self.ai_config is not None else _load_ai_config()
                cached = self._build_embedding_service(config) if config is not None else None
            self._shared_embedding = cached
        return cached

    def _get_shared_vector_store(self) -> Any:
        """容器级共享向量库（懒建一次）：knowledge 与 ingestion 共用，
        保证流水线 IndexerNode 写入的向量在 knowledge 检索侧可见（Java 单例 VectorStore）

        P6 0.1：经 _build_vector_store 按 RAGENT_VECTOR_STORE_TYPE 分派（memory 兜底；其余 fail-fast，决策 D3）。
        """
        cached = getattr(self, "_shared_vector_store", _MISSING)
        if cached is _MISSING:
            cached = self._build_vector_store()
            self._shared_vector_store = cached
        return cached

    def _build_vector_store(self) -> Any:
        """按 RAGENT_VECTOR_STORE_TYPE 装配向量后端写侧（读写双侧共享实例）

        P6 1.1：memory 为默认兜底（现状行为不变）；milvus 走 _build_milvus_stack 真实接线
        （探活 fail-fast + 集合自动创建）；1.2：pg/pgvector 走 _build_pgvector_stack 真实接线
        （CREATE EXTENSION vector 前置检查 fail-fast + 共享 HNSW 索引幂等建），并把读侧
        retriever 注入容器槽（检索引擎优先取用）。
        """
        backend = (self.settings.vector_store_type or "memory").lower()
        if backend == "memory":
            from storage.vector.in_memory import InMemoryVectorStore

            embedding = self._get_shared_embedding()
            return InMemoryVectorStore(embedding_service=embedding) if embedding is not None else None
        if backend == "milvus":
            store, retriever, _admin = self._build_milvus_stack()
            # 读侧注入槽：检索通道 vector_enabled 时优先取用（避免回落内存读侧）
            self.vector_retriever = retriever
            return store
        if backend in ("pg", "pgvector"):
            store, retriever, _admin = self._build_pgvector_stack()
            # 读侧注入槽：检索通道 vector_enabled 时优先取用（避免回落内存读侧）
            self.vector_retriever = retriever
            return store
        raise ValueError(f"未知向量后端：{backend}（允许 memory|milvus|pgvector）")

    def _build_milvus_client(self) -> Any:
        """构建并探活 pymilvus 客户端（决策 D3：连接失败即启动 fail-fast，不静默回落内存）"""
        from pymilvus import MilvusClient

        from storage.vector.milvus import PymilvusClientAdapter

        client = getattr(self, "_shared_milvus_client", _MISSING)
        if client is _MISSING:
            adapter = PymilvusClientAdapter(
                MilvusClient(
                    uri=f"http://{self.settings.milvus_host}:{self.settings.milvus_port}"
                )
            )
            adapter.list_collections()  # 探活：Milvus 不可达在此抛异常 → fail-fast
            self._shared_milvus_client = adapter
        return self._shared_milvus_client

    def _build_milvus_stack(self) -> tuple:
        """构建 Milvus 读写管理三件套（client 单例缓存；集合自动创建幂等 ensure_vector_space）

        Returns:
            (store, retriever, admin)：写侧 / 读侧 / 管理侧，共享同一 MilvusClient
        """
        from storage.vector.config import VectorProperties
        from storage.vector.milvus import (
            MilvusVectorRetrieverService,
            MilvusVectorStoreAdmin,
            MilvusVectorStoreService,
        )
        from storage.vector.schema import VectorSpaceId, VectorSpaceSpec

        cached = getattr(self, "_shared_milvus_stack", _MISSING)
        if cached is _MISSING:
            embedding = self._get_shared_embedding()
            if embedding is None:
                raise ValueError(
                    "RAGENT_VECTOR_STORE_TYPE=milvus 需要可用的 embedding 服务（ai.yaml embedding provider）"
                )
            props = VectorProperties(
                type="milvus",
                collection_name=self.settings.milvus_collection,
            )
            client = self._build_milvus_client()
            store = MilvusVectorStoreService(client, props)
            retriever = MilvusVectorRetrieverService(client, embedding, props)
            admin = MilvusVectorStoreAdmin(client, props)
            # 集合自动创建（幂等：已存在则跳过；缺失则按共享 schema 建 collection + 索引）
            admin.ensure_vector_space(
                VectorSpaceSpec(space_id=VectorSpaceId(logical_name=props.collection_name))
            )
            cached = (store, retriever, admin)
            self._shared_milvus_stack = cached
        return cached

    def _build_pgvector_stack(self) -> tuple:
        """构建 PgVector 读写管理三件套（复用 DB 的 SqlExecutor；扩展前置检查 fail-fast）

        前置（决策 D3：显式配置失败即报错，不静默回落内存）：
            1. RAGENT_DATABASE_URL 必须是 PostgreSQL 连接串（共享表/HNSW 索引/`<=>` 算子
               均为 PG + pgvector 专属，sqlite 兜底不可承载）；
            2. `CREATE EXTENSION IF NOT EXISTS vector`：扩展缺失 / 权限不足 → 启动即报错
               （含安装指引），不静默降级；
        幂等：admin.ensure_vector_space 只确保共享 HNSW 索引存在（建表依赖迁移脚本，P6 不负责）。

        Returns:
            (store, retriever, admin)：写侧 / 读侧 / 管理侧，共享同一 SqlExecutor
        """
        from storage.vector.pg import (
            PgVectorRetrieverService,
            PgVectorStoreAdmin,
            PgVectorStoreService,
        )
        from storage.vector.schema import VectorSpaceId, VectorSpaceSpec

        cached = getattr(self, "_shared_pgvector_stack", _MISSING)
        if cached is _MISSING:
            url = (self.settings.database_url or "").strip()
            if not url.lower().startswith("postgresql"):
                raise ValueError(
                    "RAGENT_VECTOR_STORE_TYPE=pgvector 需要 PostgreSQL 连接串"
                    "（RAGENT_DATABASE_URL 指向装好 pgvector 扩展的 PG，如"
                    " postgresql+psycopg://user:pwd@host:5432/db）"
                )
            if not isinstance(self.db, SqlDatabaseClient):
                raise ValueError(
                    "RAGENT_VECTOR_STORE_TYPE=pgvector 需要 real 栈的 SqlDatabaseClient"
                    "（提供 SqlExecutor），当前为 %s" % type(self.db).__name__
                )
            executor = self.db.executor
            # 前置检查：扩展 + 权限（缺失/无权限 → fail-fast，附安装指引；决策 D3）
            try:
                executor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception as ex:  # noqa: BLE001 —— 前置检查失败即启动失败
                raise ValueError(
                    "pgvector 扩展不可用或当前用户无 CREATE 权限，请先安装 pgvector"
                    "（CREATE EXTENSION vector）后再启动；原始错误: %s" % ex
                ) from ex
            embedding = self._get_shared_embedding()
            if embedding is None:
                raise ValueError(
                    "RAGENT_VECTOR_STORE_TYPE=pgvector 需要可用的 embedding 服务（ai.yaml embedding provider）"
                )
            store = PgVectorStoreService(executor)
            retriever = PgVectorRetrieverService(executor, embedding)
            admin = PgVectorStoreAdmin(executor)
            # 幂等：确保共享 HNSW 索引存在（建表依赖迁移脚本，此处只建索引，对齐 Java Admin 步骤 7）
            admin.ensure_vector_space(
                VectorSpaceSpec(space_id=VectorSpaceId(logical_name="ragent"))
            )
            cached = (store, retriever, admin)
            self._shared_pgvector_stack = cached
        return cached

    def _get_shared_vector_admin(self) -> Any:
        """容器级共享向量管理侧（懒建一次）：milvus/pgvector 复用各自 stack 的 admin，其余 InMemory 兜底"""
        cached = getattr(self, "_shared_vector_admin", _MISSING)
        if cached is _MISSING:
            backend = (self.settings.vector_store_type or "memory").lower()
            if backend == "milvus":
                cached = self._build_milvus_stack()[2]
            elif backend in ("pg", "pgvector"):
                cached = self._build_pgvector_stack()[2]
            else:
                from storage.vector.in_memory import InMemoryVectorStoreAdmin

                cached = InMemoryVectorStoreAdmin()
            self._shared_vector_admin = cached
        return cached

    def _get_shared_file_storage(self) -> Any:
        """跨域共享文件存储门面（懒构建缓存；memory 兜底 / s3 真实接线）"""
        if getattr(self, "_shared_file_storage", None) is None:
            from rag.file_storage import DefaultFileStorageService

            self._shared_file_storage = DefaultFileStorageService(
                self._build_object_storage(), self._build_storage_properties()
            )
        return self._shared_file_storage

    def _build_object_storage(self) -> Any:
        """按 RAGENT_OBJECT_STORAGE_BACKEND 装配对象存储客户端

        P6 2.1：memory 为默认兜底（现状行为不变）；s3 走 _build_s3_client 真实接线
        （探活 fail-fast + kb/asset 桶幂等自建）；oss 显式配置时 fail-fast（可选任务 2.2，
        未实现不静默回落内存，决策 D3）。
        """
        backend = (self.settings.object_storage_backend or "memory").lower()
        if backend == "memory":
            from storage.object import MemoryObjectStorageClient

            return MemoryObjectStorageClient()
        if backend == "s3":
            return self._build_s3_client()
        if backend == "oss":
            raise ValueError(
                f"RAGENT_OBJECT_STORAGE_BACKEND=oss 后端实现待 P6 任务 2.2（可选），当前仅支持 memory|s3"
            )
        raise ValueError(f"未知对象存储后端：{backend}（允许 memory|s3|oss）")

    def _build_storage_properties(self) -> Any:
        """按 RAGENT_OBJECT_STORAGE_BACKEND / RAGENT_S3_* env 构建对象存储配置（memory 兜底默认）"""
        from storage.object.config import RagStorageProperties

        backend = (self.settings.object_storage_backend or "memory").lower()
        if backend != "s3":
            return RagStorageProperties()
        return RagStorageProperties(
            type="s3",
            kb_bucket=self.settings.s3_bucket or "ragent-sources",
            asset_bucket=self.settings.s3_asset_bucket or "ragent-assets",
            s3=self._build_s3_config(),
        )

    def _build_s3_config(self) -> Any:
        """按 RAGENT_S3_* env 构建 S3 配置（endpoint 留空走 AWS 默认链）"""
        from storage.object.config import S3Config

        return S3Config(
            endpoint=self.settings.s3_endpoint,
            access_key=self.settings.s3_access_key,
            secret_key=self.settings.s3_secret_key,
            region=self.settings.s3_region,
            path_style=self.settings.s3_path_style,
            public_url=self.settings.s3_public_url,
        )

    def _create_boto3_s3_client(self, config: Any) -> Any:
        """创建 boto3 S3 客户端（endpoint/凭证/region/path-style 按配置；endpoint 留空走 AWS 默认链）"""
        import boto3
        from botocore.config import Config as BotoConfig

        kwargs: dict = {
            "region_name": config.region,
            "config": BotoConfig(
                s3={"addressing_style": "path" if config.path_style else "auto"},
                retries={"max_attempts": 3},  # SDK 自动重试（reliable_put 依赖）
            ),
        }
        if config.endpoint:
            kwargs["endpoint_url"] = config.endpoint
        if config.access_key:
            kwargs["aws_access_key_id"] = config.access_key
            kwargs["aws_secret_access_key"] = config.secret_key
        return boto3.client("s3", **kwargs)

    def _build_s3_client(self) -> Any:
        """构建 S3 兼容存储客户端（boto3，容器级单例缓存）：探活 fail-fast + kb/asset 桶幂等自建

        前置（决策 D3：显式配置失败即报错，不静默回落内存）：
            1. list_buckets 探活：endpoint 可达 + 凭证有效（不可达/凭证错 → 启动即报错）；
            2. kb/asset 桶幂等 create_bucket（已存在视为成功），对齐 Milvus 集合自动创建心智。
        """
        from storage.object.s3 import S3ObjectStorageClient

        cached = getattr(self, "_shared_s3_client", _MISSING)
        if cached is _MISSING:
            config = self._build_s3_config()
            client = self._create_boto3_s3_client(config)
            # 探活：endpoint 可达 + 凭证有效（不可达/凭证错误在此抛 → fail-fast）
            client.list_buckets()
            s3_client = S3ObjectStorageClient(client, config)
            # 幂等：确保 kb/asset 桶存在（首次启动自动建，对齐 Milvus ensure_vector_space 心智）
            for bucket in (
                self.settings.s3_bucket or "ragent-sources",
                self.settings.s3_asset_bucket or "ragent-assets",
            ):
                s3_client.create_bucket(bucket)
            cached = s3_client
            self._shared_s3_client = cached
        return cached

    def _wire_history_chat_service(self) -> None:
        """按 engine 组装 chat_service（M7 C14：真实记忆 + M6 queue_limiter）"""
        from rag.dao.conversation_dao import ConversationDao
        from rag.service.chat_service import RAGChatService
        from rag.service.ratelimit import ChatQueueLimiter
        from rag.service.stream.callback_factory import StreamCallbackFactory
        from rag.service.stream.trace_runner import StreamChatTraceRunner

        conversation_dao = ConversationDao(self.db)
        # 复用容器级记忆服务：handler 落库 + reject 落库与 engine 加载历史同一实例
        callback_factory = StreamCallbackFactory(
            memory_service=self.memory_service,
            task_manager=self.stream_task_manager,
            conversation_dao=conversation_dao,
        )
        trace_runner = StreamChatTraceRunner(record_service=self.trace_record_service)  # 宿主：M5 RagTraceRecordService
        # M6 限流装配（对齐 Java ChatRateLimiterConfig）：rate_limiter 按 backend 配置 + chat_queue_limiter
        queue_limiter = ChatQueueLimiter(
            rate_limiter=self._build_rate_limiter(),
            rate_limit_properties=self.rate_limit_properties,
            memory_service=self.memory_service,
            conversation_dao=conversation_dao,
        )
        self.chat_service = RAGChatService(
            callback_factory=callback_factory,
            engine=self.engine,
            task_manager=self.stream_task_manager,
            trace_runner=trace_runner,
            queue_limiter=queue_limiter,
        )

    def _build_rate_limiter(self) -> Any:
        """按 `rate_limit_backend` 装配 FairRateLimiter（对齐 Java ChatRateLimiterConfig.chatRateLimiter）

        - process：ProcessFairRateLimiter（单机，6.2，缺省）；
        - redis：  RedisFairRateLimiter（分布式，6.3，需注入 `container.redis` 客户端，未注入 fail-fast）。
        """
        from rag.service.ratelimit import ProcessFairRateLimiter, RedisFairRateLimiter

        props = self.rate_limit_properties
        common = dict(
            max_concurrent=props.global_max_concurrent,
            default_wait_seconds=float(props.global_max_wait_seconds),
            enabled=props.global_enabled,
            lease_seconds=props.global_lease_seconds,
            poll_interval_ms=props.global_poll_interval_ms,
        )
        backend = self.settings.rate_limit_backend
        if backend == "process":
            return ProcessFairRateLimiter(**common)
        if backend == "redis":
            if self.redis is None:
                raise ValueError(
                    "RAGENT_RATE_LIMIT_BACKEND=redis 需注入 redis.asyncio 客户端（container.redis）"
                )
            return RedisFairRateLimiter(
                name="rag:global:chat", client=self.redis, **common
            )
        raise ValueError(f"未知限流后端：{backend}（允许 process|redis）")

    # ==================== 生命周期 ====================

    def _wire_knowledge_services(self) -> None:
        """组装 P5 knowledge 域（N1-N3：KB/文档/分块 service + 支撑组件，plan 5.3.5）

        chunk_dao/chunk_service/vector_store 回注 document_service：enable 双向向量同步、
        page 的 chunks_edited 标记随 N3 注入生效。embedding 复用 ai_config 构建的实例；
        无可用 embedding 客户端时向量侧退化为「仅关系库落库」（分块/检索受限于无向量后端）。
        """
        from knowledge.dao.base import KnowledgeBaseDao
        from knowledge.dao.chunk import KnowledgeChunkDao
        from knowledge.dao.chunk_log import KnowledgeDocumentChunkLogDao
        from knowledge.dao.document import KnowledgeDocumentDao
        from knowledge.dao.schedule import KnowledgeDocumentScheduleDao
        from knowledge.dao.schedule_exec import KnowledgeDocumentScheduleExecDao
        from knowledge.filter.upload_rate_limiter import UploadRateLimiter
        from knowledge.handler.remote_file_fetcher import RemoteFileFetcher
        from knowledge.mq.chunk_dispatcher import ProcessChunkTaskDispatcher
        from knowledge.schedule.job import KnowledgeDocumentScheduleJob
        from knowledge.schedule.lock_manager import ScheduleLockManager
        from knowledge.schedule.refresh_processor import ScheduleRefreshProcessor
        from knowledge.schedule.state_manager import ScheduleStateManager
        from knowledge.schedule.status_helper import DocumentStatusHelper
        from knowledge.service.base import KnowledgeBaseService
        from knowledge.service.chunk import KnowledgeChunkService
        from knowledge.service.document import KnowledgeDocumentService
        from knowledge.service.schedule import KnowledgeDocumentScheduleService
        from knowledge.sink.relational_chunk_sink import RelationalChunkSink
        from knowledge.support.ingestion_spec_codec import IngestionSpecCodec
        from knowledge.support.ingestion_spec_schema import IngestionSpecSchemaProvider
        from knowledge.support.vector_target_resolver import VectorTargetResolver
        from rag.ingestion.kernel import ChunkEmbeddingService, DefaultIngestionKernel
        from rag.ingestion.sink import ChunkIndexWriter, VectorStoreSink
        from rag.ingestion.splitter.base import ChunkingService

        # 解析器注册表 + 摄取配置 codec/schema（schema 档位推导依赖注册表；MinerU 条件注册见 build_parser_registry）
        parser_registry = build_parser_registry(self._get_shared_file_storage())
        codec = IngestionSpecCodec()
        schema_provider = IngestionSpecSchemaProvider(parser_registry)

        # 文件存储门面（P6 2.1 按 RAGENT_OBJECT_STORAGE_BACKEND 分派；memory 兜底 / s3 真实接线 / oss 2.2 可选）+ 远端拉取 + 上传限流
        file_storage = self._get_shared_file_storage()
        fetcher = RemoteFileFetcher(file_storage)
        limiter = UploadRateLimiter()

        # 向量/嵌入（复用 ai_config 构建 embedding；无可用 embedding → 向量侧退化）
        ai_config = self.ai_config if self.ai_config is not None else _load_ai_config()
        embedding = self._get_shared_embedding()  # 跨域共享（knowledge/ingestion 同一实例）
        resolver = VectorTargetResolver(ai_config)
        chunk_embedding = ChunkEmbeddingService(embedding) if embedding is not None else None
        vector_store = self._get_shared_vector_store()  # 跨域共享（流水线写入可被检索读到）
        vector_admin = self._get_shared_vector_admin()

        # 扇出：向量 + 关系库 chunk（N0 RelationalChunkSink 并入；无向量后端仅关系库）。
        # 向量库须经 VectorStoreSink 桥接（裸 store 无 replace_document 契约）
        sinks = []
        if vector_store is not None:
            sinks.append(VectorStoreSink(vector_store))
        sinks.append(RelationalChunkSink(self.db))
        chunk_index_writer = ChunkIndexWriter(sinks)

        # 摄取内核：parser → chunk → embed → 扇出
        ingest_kernel = DefaultIngestionKernel(
            parser_registry, ChunkingService(), chunk_embedding, chunk_index_writer
        )

        # dao
        kb_dao = KnowledgeBaseDao(self.db)
        doc_dao = KnowledgeDocumentDao(self.db)
        chunk_dao = KnowledgeChunkDao(self.db)
        chunk_log_dao = KnowledgeDocumentChunkLogDao(self.db)
        schedule_dao = KnowledgeDocumentScheduleDao(self.db)
        schedule_exec_dao = KnowledgeDocumentScheduleExecDao(self.db)

        # 服务：dispatcher 的 start_chunk 事务体经延迟闭包引用 document_service（循环依赖：dispatcher ↔ doc_service）
        document_service = None  # 先占位，闭包在 dispatch 时已绑定

        def _dispatcher_start(doc_id: str, operator) -> None:
            document_service._cas_start_chunk(doc_id, operator)

        dispatcher = ProcessChunkTaskDispatcher(
            start_chunk=_dispatcher_start,
            execute_chunk=lambda doc_id: document_service.execute_chunk(doc_id),
            max_concurrent=2,
        )

        self.knowledge_base_service = KnowledgeBaseService(
            kb_dao, doc_dao, file_storage=file_storage, vector_admin=vector_admin
        )
        self.knowledge_chunk_service = KnowledgeChunkService(
            chunk_dao=chunk_dao, doc_dao=doc_dao, kb_dao=kb_dao,
            chunk_embedding_service=chunk_embedding,
            vector_target_resolver=resolver,
            vector_store=vector_store,
        )
        # N4 调度域：登记服务 + 行锁 + 状态回写 + 状态机助手 + 刷新处理器 + 调度任务
        self.knowledge_schedule_service = KnowledgeDocumentScheduleService(
            schedule_dao, schedule_exec_dao, min_interval_seconds=60,
        )
        lock_manager = ScheduleLockManager(schedule_dao, lock_seconds=900)
        status_helper = DocumentStatusHelper(self.db, doc_dao)
        state_manager = ScheduleStateManager(schedule_dao, schedule_exec_dao)
        self.knowledge_document_service = KnowledgeDocumentService(
            kb_dao=kb_dao, doc_dao=doc_dao, chunk_log_dao=chunk_log_dao,
            parser_registry=parser_registry, codec=codec,
            vector_target_resolver=resolver,
            ingest_kernel=ingest_kernel, chunk_index_writer=chunk_index_writer,
            file_storage=file_storage, fetcher=fetcher, dispatcher=dispatcher,
            limiter=limiter,
            chunk_dao=chunk_dao, chunk_service=self.knowledge_chunk_service,
            vector_store=vector_store,
            schedule_service=self.knowledge_schedule_service,  # N4：delete/update 调度行清理
            pipeline_service=getattr(self, "ingestion_pipeline_service", None),  # N5 接入：PIPELINE 模式校验 + pipelineName 回填
        )
        document_service = self.knowledge_document_service
        refresh_processor = ScheduleRefreshProcessor(
            schedule_dao=schedule_dao, exec_dao=schedule_exec_dao,
            doc_dao=doc_dao, kb_dao=kb_dao,
            document_service=document_service, file_storage=file_storage,
            fetcher=fetcher, lock_manager=lock_manager,
            state_manager=state_manager, status_helper=status_helper,
        )
        self.knowledge_schedule_job = KnowledgeDocumentScheduleJob(
            schedule_dao=schedule_dao, lock_manager=lock_manager,
            refresh_processor=refresh_processor, status_helper=status_helper,
            scan_delay_ms=10000, batch_size=20, running_timeout_minutes=30,
        )
        self.ingestion_spec_schema_provider = schema_provider

    def _wire_ingestion_services(self) -> None:
        """组装 P5 N5 摄取流水线域（plan 7.2：dao×4 → engine(7 节点) → pipeline/task 服务）

        意图树管理（N5 5.5 IntentTreeService）复用 M5 既有 IntentTreeAdminService
        （rag/service/intent_tree_admin_service.py，M5 5.4 已覆盖 CRUD + 缓存清理），不重复装配。

        引擎节点条件装配：无可用 LLM → 跳过 enhancer/enricher；无可用 embedding → 跳过
        chunker/indexer（流水线引用即报「未找到节点类型」，干净失败而非 AttributeError）。
        """
        from ingestion.dao.pipeline import IngestionPipelineDao
        from ingestion.dao.pipeline_node import IngestionPipelineNodeDao
        from ingestion.dao.task import IngestionTaskDao
        from ingestion.dao.task_node import IngestionTaskNodeDao
        from ingestion.engine.engine import IngestionEngine
        from ingestion.node.chunker_node import ChunkerNode
        from ingestion.node.enhancer_node import EnhancerNode
        from ingestion.node.enricher_node import EnricherNode
        from ingestion.node.fetcher_node import FetcherNode
        from ingestion.node.indexer_node import IndexerNode
        from ingestion.node.parser_node import ParserNode
        from ingestion.service.pipeline import IngestionPipelineService
        from ingestion.service.task import IngestionTaskService
        from ingestion.strategy.fetcher.feishu_fetcher import FeishuFetcher
        from ingestion.strategy.fetcher.http_url_fetcher import HttpUrlFetcher
        from ingestion.util.http_client_helper import HttpClientHelper
        from rag.ingestion.kernel import ChunkEmbeddingService
        from rag.ingestion.splitter.base import ChunkingService

        # dao ×4（pipeline/node/task/task_node）
        pipeline_dao = IngestionPipelineDao(self.db)
        pipeline_node_dao = IngestionPipelineNodeDao(self.db)
        task_dao = IngestionTaskDao(self.db)
        task_node_dao = IngestionTaskNodeDao(self.db)

        # engine：7 节点条件装配（LLM / embedding 缺失时跳过对应节点）
        ai_config = self.ai_config if self.ai_config is not None else _load_ai_config()
        embedding = self._get_shared_embedding()  # 跨域共享（knowledge/ingestion 同一实例）
        llm = self._get_shared_llm()  # 跨域共享（chat 引擎同一 LLM 路由，熔断状态同源）

        parser_registry = build_parser_registry(self._get_shared_file_storage())
        chunk_embedding = ChunkEmbeddingService(embedding) if embedding is not None else None
        vector_store = self._get_shared_vector_store()  # 跨域共享（写入可被 knowledge 检索读到）
        vector_admin = self._get_shared_vector_admin()

        http = HttpClientHelper()
        fetchers = [HttpUrlFetcher(http), FeishuFetcher(http)]
        nodes = [FetcherNode(fetchers), ParserNode(parser_registry)]
        if chunk_embedding is not None:
            nodes.append(ChunkerNode(ChunkingService(), chunk_embedding))
        if llm is not None:
            nodes.append(EnhancerNode(llm))
            nodes.append(EnricherNode(llm))
        if vector_store is not None:
            nodes.append(IndexerNode(vector_store, vector_admin))
        engine = IngestionEngine(nodes)

        # 服务：task 依赖 pipeline_service（get_definition）
        self.ingestion_pipeline_service = IngestionPipelineService(pipeline_dao, pipeline_node_dao)
        self.ingestion_task_service = IngestionTaskService(
            engine, self.ingestion_pipeline_service, task_dao, task_node_dao
        )

    def close(self) -> None:
        """释放容器持有的同步资源（测试 / 非 async 场景直接调用）"""
        for obj in self._owned:
            closer = getattr(obj, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 释放失败不阻断关闭
                    logger.warning("装配对象释放失败: %s", obj, exc_info=True)

    async def aclose(self) -> None:
        """释放容器持有的全部资源（lifespan 退出调用）：同步 _owned + redis 连接池优雅断开

        P6 3.1：redis.asyncio 客户端须经 aclose() 异步断开（redis-py>=5 `close()` 已废弃为协程），
        无 redis 注入时与 close() 等价；释放失败不阻断关闭。
        """
        self.close()
        if self.redis is not None:
            acloser = getattr(self.redis, "aclose", None)
            if callable(acloser):
                try:
                    await acloser()
                except Exception:  # noqa: BLE001 释放失败不阻断关闭
                    logger.warning("redis 连接池关闭失败", exc_info=True)


class _McpAutoconfigCloser:
    """把 McpClientAutoConfiguration.destroy() 适配为容器 _owned 的 close() 约定"""

    def __init__(self, autoconfig: Any) -> None:
        self._autoconfig = autoconfig

    def close(self) -> None:
        self._autoconfig.destroy()
