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
        # M5 5.4 意图树管理（写后清 intent 树缓存）
        self.intent_tree_admin_service = IntentTreeAdminService(
            IntentNodeAdminDao(self.db),
            cache_manager=RedisIntentTreeCacheManager(cache_manager=self.cache),
        )
        # M5 5.5 Agent 档案管理（提示词读路径共享同一缓存实例）
        prompt_cache = RedisAgentPromptCacheManager(cache_manager=self.cache)
        orchestration_mode = OrchestrationMode.of(self.settings.orchestration_mode)
        self.agent_profile_admin_service = AgentProfileAdminService(
            profile_dao=AgentProfileDao(self.db),
            prompt_dao=AgentPromptDao(self.db),
            resolver=DatabaseAgentPromptResolver(self.db, cache_manager=prompt_cache),
            prompt_cache_manager=prompt_cache,
            mode=orchestration_mode,  # 从 AppSettings 回注，槽位生效集随编排模式
        )
        # M5 5.6 设置聚合（ai 模型配置待 engine 装配注入）
        self.rate_limit_properties = RateLimitProperties.from_env()
        self.settings_service = SystemSettingsService(
            memory_properties=MemoryProperties(),
            query_rewrite_enabled=True,
            citation_enabled=False,
            orchestration_mode=orchestration_mode.value,  # 与 5.5 槽位生效集同源
            rate_limit=self.rate_limit_properties,  # 单真源：直接收 RateLimitProperties
        )
        # M5 5.7 图谱可视化（C12，委托既有 GraphQueryService）
        self.graph_service = GraphQueryService()

    def _wire_chat_services(self) -> None:
        """组装流式/聊天依赖（M3 切片）：任务管理器 + 幂等守卫（cache 真实依赖）；chat_service 待 engine 装配（M7 C14）"""
        from rag.service.idempotent import IdempotentSubmitGuard
        from rag.service.stream.task_manager import StreamTaskManager

        self.stream_task_manager = StreamTaskManager(cache=self.cache)
        self.idempotent_guard = IdempotentSubmitGuard(cache=self.cache)
        if self.engine is not None:
            self._wire_history_chat_service()

    def _wire_history_chat_service(self) -> None:
        """按 engine 组装 chat_service（M7 C14 注入 engine + memory_service 后调用）"""
        from rag.dao.conversation_dao import ConversationDao
        from rag.memory import (
            DatabaseConversationMemoryStore,
            DefaultConversationMemoryService,
            MemoryConversationMemorySummaryService,
        )
        from rag.memory.config import MemoryProperties
        from rag.service.chat_service import RAGChatService
        from rag.service.ratelimit import ChatQueueLimiter
        from rag.service.stream.callback_factory import StreamCallbackFactory
        from rag.service.stream.trace_runner import StreamChatTraceRunner

        conversation_dao = ConversationDao(self.db)
        # 真实记忆服务（M6 起）：reject 落库（用户问题 + REJECTED 回复保留会话记录）+ 正常消息持久化；
        # M7 engine 装配可复用同一服务加载历史（store 的 message_id 由共享计数器保证并发安全）
        memory_properties = MemoryProperties()
        memory_service = DefaultConversationMemoryService(
            memory_store=DatabaseConversationMemoryStore(self.db, memory_properties),
            summary_service=MemoryConversationMemorySummaryService(properties=memory_properties),
        )
        callback_factory = StreamCallbackFactory(
            memory_service=memory_service,
            task_manager=self.stream_task_manager,
            conversation_dao=conversation_dao,
        )
        trace_runner = StreamChatTraceRunner(record_service=self.trace_record_service)  # 宿主：M5 RagTraceRecordService
        # M6 限流装配（对齐 Java ChatRateLimiterConfig）：rate_limiter 按 backend 配置 + chat_queue_limiter
        queue_limiter = ChatQueueLimiter(
            rate_limiter=self._build_rate_limiter(),
            rate_limit_properties=self.rate_limit_properties,
            memory_service=memory_service,
            conversation_dao=conversation_dao,
            memory_properties=memory_properties,
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

    def close(self) -> None:
        """释放容器持有的资源（lifespan 退出时调用）"""
        for obj in self._owned:
            closer = getattr(obj, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 释放失败不阻断关闭
                    logger.warning("装配对象释放失败: %s", obj, exc_info=True)
