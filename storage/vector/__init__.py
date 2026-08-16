"""
storage.vector - 向量库后端实现

当前提供：
    - schema：向量空间/落点契约（VectorSpaceId / VectorSpaceSpec / VectorTarget）
    - InMemoryVectorStore：内存版（开发 / 测试 / 演示）

后续补充：
    - Milvus 实现（对应 ragent MilvusVectorStoreService / MilvusVectorRetrieverService）
    - PgVector 实现（对应 ragent PgVectorStoreService / PgVectorRetrieverService）
"""
from storage.vector.in_memory import InMemoryVectorStore
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec, VectorTarget

__all__ = ["InMemoryVectorStore", "VectorSpaceId", "VectorSpaceSpec", "VectorTarget"]
