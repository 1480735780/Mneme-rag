"""
向量空间与落点 schema（对应 Java VectorSpaceId / VectorSpaceSpec / VectorTarget）

定义向量库适配层（storage/vector）与入库链路（rag/ingestion）共享的契约：

    - VectorSpaceId：   物理空间标识。逻辑名（logical_name）对业务层暴露，跨引擎保持一致；
                        namespace 是可选物理前缀（milvus database / ES 索引前缀 等）。
    - VectorSpaceSpec：  向量空间规格（空间标识 + 备注），供向量空间管理（创建/查询）使用。
    - VectorTarget：     向量落点身份。块写到哪个逻辑分区、用哪个模型、必须是多少维，
                        由知识库配置（L2）与部署配置（L1）合成。

注意：VectorTarget.partition 是逻辑分区键，与 VectorSpaceId 表示的物理空间
（PG 下是共享表与共享索引、Milvus 下是 collection）不是一回事，两者都别叫 collectionName；
模型与维度随身携带，缺一个都不允许落到系统默认值。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.VectorSpaceId
    - com.nageoffer.ai.ragent.rag.core.vector.VectorSpaceSpec
    - com.nageoffer.ai.ragent.core.ingest.VectorTarget
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class VectorSpaceId:
    """
    向量空间标识（对应 Java VectorSpaceId）

    Attributes:
        logical_name: 逻辑名称：对业务层暴露的名字，跨引擎保持一致（如 kb_employee_policy）
        namespace:    可选：命名空间 / 数据库 / 索引前缀（如 milvus database / ES 索引前缀）
    """
    logical_name: str
    namespace: Optional[str] = None

    def __post_init__(self):
        if not self.logical_name or not self.logical_name.strip():
            raise ValueError("logical_name 不能为空")


@dataclass
class VectorSpaceSpec:
    """
    向量空间规格（对应 Java VectorSpaceSpec）

    Attributes:
        space_id: 向量空间标识
        remark:   备注
    """
    space_id: VectorSpaceId
    remark: Optional[str] = None


@dataclass
class VectorTarget:
    """
    向量落点身份（对应 Java VectorTarget）

    Attributes:
        partition:       逻辑分区键，取自知识库的 collection_name（非物理空间名）
        embedding_model: 嵌入模型 ID，取自知识库配置，不允许回落到系统默认
        dimension:       向量维度，取自部署级配置，全局硬约束
    """
    partition: str
    embedding_model: str
    dimension: int

    def __post_init__(self):
        if not self.partition or not self.partition.strip():
            raise ValueError("partition 不能为空")
        if not self.embedding_model or not self.embedding_model.strip():
            raise ValueError(
                f"embedding_model 不能为空，partition={self.partition}"
                "——嵌入模型是知识库级约束性配置，不允许回落到系统默认"
            )
        if self.dimension <= 0:
            raise ValueError(f"dimension 必须 > 0，实际 {self.dimension}")
