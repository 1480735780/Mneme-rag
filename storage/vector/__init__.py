"""
storage.vector - 向量库后端实现

当前提供：
    - schema：向量空间/落点契约（VectorSpaceId / VectorSpaceSpec / VectorTarget）
    - config：向量后端配置（VectorProperties：type / collection_name / dimension / metric_type）
    - InMemoryVectorStore：内存版（开发 / 测试 / 演示）
    - InMemoryVectorStoreAdmin：内存版向量空间管理
    - MilvusVectorStoreService：Milvus 写侧（共享 collection + content 截断 + 维度校验）
    - MilvusVectorRetrieverService：Milvus 读侧（共享 collection 单次跨库检索 + 标量过滤）
    - MilvusVectorStoreAdmin：Milvus 空间管理（幂等建共享 collection + HNSW(COSINE) + 倒排索引）
    - PgVectorStoreService：PgVector 写侧（共享表 + ON CONFLICT + JSON 路径删除，经 SqlExecutor）
    - PgVectorRetrieverService：PgVector 读侧（<=> 余弦距离 + ef_search，经 SqlExecutor）
    - PgVectorStoreAdmin：PgVector 空间管理（共享 HNSW 索引 + 按库删行，经 SqlExecutor）
    - CollectionParallelRetriever：逐库并行兜底策略（不支持跨库单查时的 fan-out）
    - decorator：向量写入同步装饰器抽象接口（GraphSyncing / KeywordSyncing，契约先行、实现待补）
"""
from storage.vector.config import VectorProperties
from storage.vector.decorator import (
    GraphSyncingVectorStoreService,
    KeywordSyncingVectorStoreService,
)
from storage.vector.in_memory import InMemoryVectorStore, InMemoryVectorStoreAdmin
from storage.vector.milvus import (
    MilvusVectorRetrieverService,
    MilvusVectorStoreAdmin,
    MilvusVectorStoreService,
)
from storage.vector.pg import (
    PgVectorRetrieverService,
    PgVectorStoreAdmin,
    PgVectorStoreService,
)
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec, VectorTarget
from storage.vector.strategy import CollectionParallelRetriever

__all__ = [
    "VectorProperties",
    "InMemoryVectorStore",
    "InMemoryVectorStoreAdmin",
    "MilvusVectorStoreService",
    "MilvusVectorRetrieverService",
    "MilvusVectorStoreAdmin",
    "PgVectorStoreService",
    "PgVectorRetrieverService",
    "PgVectorStoreAdmin",
    "CollectionParallelRetriever",
    "GraphSyncingVectorStoreService",
    "KeywordSyncingVectorStoreService",
    "VectorSpaceId",
    "VectorSpaceSpec",
    "VectorTarget",
]
