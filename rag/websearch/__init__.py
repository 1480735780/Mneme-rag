"""
rag.websearch - 联网检索

    - client：WebSearchClient 抽象 + MemoryWebSearchClient + MemoryWebResult（MVP 内存占位实现）
      + YouComWebSearchClient（真实 You.com Search API HTTP 实现）

对应 ragent 源码：
    - rag/core/retrieval/channel/WebSearchChannel（You.com 调用部分）
"""
from rag.websearch.client import (
    MemoryWebResult,
    MemoryWebSearchClient,
    WebSearchClient,
    YouComWebSearchClient,
)

__all__ = [
    "MemoryWebResult",
    "MemoryWebSearchClient",
    "WebSearchClient",
    "YouComWebSearchClient",
]