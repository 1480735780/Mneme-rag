"""
storage.vector - 向量库后端实现

当前提供：
    - InMemoryVectorStore：内存版（开发 / 测试 / 演示）

后续补充：
    - Milvus 实现（对应 ragent MilvusVectorStoreService / MilvusVectorRetrieverService）
"""
from storage.vector.in_memory import InMemoryVectorStore

__all__ = ["InMemoryVectorStore"]
