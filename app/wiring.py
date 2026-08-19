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
from rag.dao.conversation_dao import ConversationDao
from rag.dao.feedback_dao import MessageFeedbackDao
from rag.dao.message_dao import MessageDao
from rag.dao.summary_dao import ConversationSummaryDao
from rag.memory.config import MemoryProperties
from rag.prompt.formatter import PromptTemplateLoader
from rag.service.conversation_service import (
    ConversationService,
    ConversationTitleGenerator,
)
from rag.service.message_service import ConversationMessageService
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
    conversation_service: Optional[ConversationService] = None
    message_service: Optional[ConversationMessageService] = None
    engine: Any = None
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
