"""
rag - RAG 核心编排层

    - engine：RAG 对话引擎（StreamChatPipeline 对应物）：记忆 → 改写 → 意图 → 引导 → 检索 → 生成
    - source：引用与来源组装
    - prompt：提示词编排与上下文格式化
    - rewrite：查询改写与术语归一化
    - intent：意图解析
    - guidance：歧义引导
    - retrieval：多通道检索（引擎 / DTO / 通道 / 后处理器）
"""
from rag.engine import (
    ConversationMemoryService,
    NoopConversationMemoryService,
    RAGChatEngine,
    StreamChatContext,
)
from rag.retrieval.schema import (
    MULTI_CHANNEL_KEY,
    RetrievalContext,
)

__all__ = [
    "ConversationMemoryService",
    "NoopConversationMemoryService",
    "RAGChatEngine",
    "StreamChatContext",
    "RetrievalContext",
    "MULTI_CHANNEL_KEY",
]
