"""
rag.memory - 会话记忆

    - config：MemoryProperties（历史保留轮数 / 摘要压缩 / 标题长度配置）
    - store：ConversationMemoryStore（存储 SPI）+ MemoryConversationMemoryStore（进程内）+ DatabaseConversationMemoryStore（DatabaseClient 实现）
    - summary：ConversationMemorySummaryService（摘要 SPI）+ MemoryConversationMemorySummaryService（进程内）+ DatabaseConversationMemorySummaryService（DatabaseClient + LLM 真实压缩）
    - service：DefaultConversationMemoryService（编排门面，替换 A 层 Noop）

对应 ragent 源码：
    - rag/config/MemoryProperties
    - rag/core/memory/ConversationMemoryStore
    - rag/core/memory/JdbcConversationMemoryStore
    - rag/core/memory/ConversationMemorySummaryService
    - rag/core/memory/JdbcConversationMemorySummaryService
    - rag/core/memory/DefaultConversationMemoryService
"""
from rag.memory.config import MemoryProperties
from rag.memory.service import DefaultConversationMemoryService
from rag.memory.store import (
    ConversationMemoryStore,
    DatabaseConversationMemoryStore,
    MemoryConversationMemoryStore,
)
from rag.memory.summary import (
    ConversationMemorySummaryService,
    DatabaseConversationMemorySummaryService,
    MemoryConversationMemorySummaryService,
    SummaryGenerator,
)

__all__ = [
    "MemoryProperties",
    "ConversationMemoryStore",
    "MemoryConversationMemoryStore",
    "DatabaseConversationMemoryStore",
    "ConversationMemorySummaryService",
    "MemoryConversationMemorySummaryService",
    "DatabaseConversationMemorySummaryService",
    "SummaryGenerator",
    "DefaultConversationMemoryService",
]
