"""
向量后端配置（对应 ragent RAGDefaultProperties + rag.vector.type 装配）

Milvus 与 PgVector 共享同一读写接口，二选一按 type 装配：
    - type=milvus（默认，对齐 Java MilvusVectorStoreService 的 matchIfMissing=true）
    - type=pg（后续 5.2 步骤 5 接入）

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.config.RAGDefaultProperties
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VectorProperties:
    """
    向量后端配置（对应 Java RAGDefaultProperties）

    Attributes:
        type:            后端类型，可选 milvus（默认）/ pg
        collection_name: 全部知识库共用的物理 collection 名称（对应 Java rag.default.collection-name）
        dimension:       向量维度，须与所用 Embedding 模型输出维度一致（对应 Java rag.default.dimension）
        metric_type:     相似度度量类型（COSINE / L2 / IP，对应 Java rag.default.metric-type）
    """

    type: str = "milvus"
    collection_name: str = "default_collection"
    dimension: int = 1024
    metric_type: str = "COSINE"

    def shared_collection(self) -> str:
        """
        全部知识库共用的物理 collection 名称（对应 Java MilvusVectorStoreService.sharedCollection）

        与关键词共享索引、PG 共享表同构：单 collection 承载所有知识库，按 collection_name 字段区分。
        """
        return self.collection_name

    def is_milvus(self) -> bool:
        """是否启用 milvus 后端"""
        return self.type.lower() == "milvus"
