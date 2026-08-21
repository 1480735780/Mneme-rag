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
    Ollama / SiliconFlow 暂无 chat 客户端实现 → 跳过（对应模型候选会因无客户端而在路由时 fail-over）。
    """
    from core.llm.providers.openai import OpenAIChatClient
    from core.llm.providers.qwen import QwenChatClient

    factory = {"qwen": QwenChatClient, "openai": OpenAIChatClient}
    clients: list = []
    for name, provider in config.providers.items():
        client_cls = factory.get(name)
        if client_cls is None:
            logger.warning("provider %s 暂无 chat client 实现，跳过", name)
            continue
        api_key = str(getattr(provider, "api_key", "") or "").strip()
        if not api_key or api_key.startswith("${"):
            # 占位符未解析（如 ${QWEN_API_KEY} 未设环境变量）→ 视为无 key，不进入候选
            logger.info("provider %s 未配置有效 api_key（占位符未解析），跳过", name)
            continue
        try:
            clients.append(client_cls())
        except Exception as ex:  # noqa: BLE001 —— 单 provider 构建失败不阻断其余
            logger.warning("provider %s chat client 构建失败: %s", name, ex)
    return clients


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
    _owned: list = field(default_factory=list)

    # ==================== 双 profile 装配 ====================

    @classmethod
    def build(cls, settings: Optional[AppSettings] = None) -> "AppContainer":
        """按配置选择装配栈（对齐 Java @ConditionalOnProperty 语义）"""
        settings = settings or AppSettings.from_env()
        if settings.is_memory():
            return cls._build_memory(settings)
        return cls._build_real(settings)

    @classmethod
    def _build_memory(cls, settings: AppSettings) -> "AppContainer":
        """全内存栈（InMemory DB + Memory 缓存），测试/演示 profile"""
        db = InMemoryDatabaseClient()
        db.ensure_schema(DEFAULT_TABLES)
        container = cls(settings=settings, db=db, cache=MemoryCacheManager())
        container._wire_conversation_services()
        container._wire_knowledge_services()
        container._wire_chat_services()
        return container

    @classmethod
    def _build_real(cls, settings: AppSettings) -> "AppContainer":
        """真实栈（SqlDatabaseClient + RedisCacheManager），env 驱动

        M0 骨架：默认 SQLite 内存库兜底（SqlAlchemySqlExecutor），Redis 未注入时用 Memory 兜底，
        真实 PG/Redis 连接串随 M1/M3 接线时经配置注入。LLM 路由（标题生成）M3 注入前回退默认标题。
        """
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        db = SqlDatabaseClient(SqlAlchemySqlExecutor(engine=engine))
        db.ensure_schema(DEFAULT_TABLES)
        container = cls(settings=settings, db=db, cache=MemoryCacheManager())
        container._wire_conversation_services()
        container._wire_knowledge_services()
        container._wire_chat_services()
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
        llm = self.llm_service if self.llm_service is not None else self._build_llm()
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
        """构建内存向量读侧（InMemoryVectorStore + ai.yaml embedding 服务）；无 embedding 客户端 → None"""
        config = self.ai_config if self.ai_config is not None else _load_ai_config()
        embedding = self._build_embedding_service(config) if config is not None else None
        if embedding is None:
            logger.warning("向量通道启用但无可用 embedding 客户端，跳过向量通道")
            return None
        from storage.vector.in_memory import InMemoryVectorStore

        return InMemoryVectorStore(embedding_service=embedding)

    def _build_embedding_service(self, config: Any) -> Any:
        """按 ai.yaml 构建路由式向量化服务（镜像 _build_chat_clients：已知 embedding provider + 缺 key 跳过）"""
        from core.llm.embedding import RoutingEmbeddingService
        from core.llm.model.health_store import ModelHealthStore
        from core.llm.model.selector import ModelSelector
        from core.llm.model.routing_executor import RoutingExecutor
        from core.llm.providers.ollama_embedding import OllamaEmbeddingClient
        from core.llm.providers.siliconflow_embedding import SiliconFlowEmbeddingClient

        factory = {"ollama": OllamaEmbeddingClient, "siliconflow": SiliconFlowEmbeddingClient}
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
        from knowledge.filter.upload_rate_limiter import UploadRateLimiter
        from knowledge.handler.remote_file_fetcher import RemoteFileFetcher
        from knowledge.mq.chunk_dispatcher import ProcessChunkTaskDispatcher
        from knowledge.service.base import KnowledgeBaseService
        from knowledge.service.chunk import KnowledgeChunkService
        from knowledge.service.document import KnowledgeDocumentService
        from knowledge.sink.relational_chunk_sink import RelationalChunkSink
        from knowledge.support.ingestion_spec_codec import IngestionSpecCodec
        from knowledge.support.ingestion_spec_schema import IngestionSpecSchemaProvider
        from knowledge.support.vector_target_resolver import VectorTargetResolver
        from rag.file_storage import DefaultFileStorageService
        from rag.ingestion.kernel import ChunkEmbeddingService, DefaultIngestionKernel
        from rag.ingestion.parser.markdown_parser import MarkdownDocumentParser
        from rag.ingestion.parser.registry import ParserRegistry
        from rag.ingestion.parser.text_parser import TextDocumentParser
        from rag.ingestion.sink import ChunkIndexWriter
        from rag.ingestion.splitter.base import ChunkingService
        from storage.object import MemoryObjectStorageClient
        from storage.object.config import RagStorageProperties
        from storage.vector.in_memory import InMemoryVectorStore, InMemoryVectorStoreAdmin

        # 解析器注册表 + 摄取配置 codec/schema（schema 档位推导依赖注册表）
        parser_registry = ParserRegistry([TextDocumentParser(), MarkdownDocumentParser()])
        codec = IngestionSpecCodec()
        schema_provider = IngestionSpecSchemaProvider(parser_registry)

        # 文件存储门面（Memory 后端；真实 S3/OSS P6）+ 远端拉取 + 上传限流
        file_storage = DefaultFileStorageService(MemoryObjectStorageClient(), RagStorageProperties())
        fetcher = RemoteFileFetcher(file_storage)
        limiter = UploadRateLimiter()

        # 向量/嵌入（复用 ai_config 构建 embedding；无可用 embedding → 向量侧退化）
        ai_config = self.ai_config if self.ai_config is not None else _load_ai_config()
        embedding = self._build_embedding_service(ai_config) if ai_config is not None else None
        resolver = VectorTargetResolver(ai_config)
        chunk_embedding = ChunkEmbeddingService(embedding) if embedding is not None else None
        vector_store = InMemoryVectorStore(embedding_service=embedding) if embedding is not None else None
        vector_admin = InMemoryVectorStoreAdmin()

        # 扇出：向量 + 关系库 chunk（N0 RelationalChunkSink 并入；无向量后端仅关系库）
        sinks = []
        if vector_store is not None:
            sinks.append(vector_store)
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
        self.knowledge_document_service = KnowledgeDocumentService(
            kb_dao=kb_dao, doc_dao=doc_dao, chunk_log_dao=chunk_log_dao,
            parser_registry=parser_registry, codec=codec,
            vector_target_resolver=resolver,
            ingest_kernel=ingest_kernel, chunk_index_writer=chunk_index_writer,
            file_storage=file_storage, fetcher=fetcher, dispatcher=dispatcher,
            limiter=limiter,
            chunk_dao=chunk_dao, chunk_service=self.knowledge_chunk_service,
            vector_store=vector_store,
            schedule_service=None,  # N4 接入
            pipeline_service=None,  # N5 接入
        )
        document_service = self.knowledge_document_service
        self.ingestion_spec_schema_provider = schema_provider

    def close(self) -> None:
        """释放容器持有的资源（lifespan 退出时调用）"""
        for obj in self._owned:
            closer = getattr(obj, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 释放失败不阻断关闭
                    logger.warning("装配对象释放失败: %s", obj, exc_info=True)
