"""
分块元数据解析器（对应 ragent ChunkMetadataResolver）

检索命中的 chunkId（等于向量库主键，也等于 t_knowledge_chunk.id）批量回表，
补齐其所属文档信息（文档ID、文档内序号、文档标题），供上下文组装时按文档聚合与标注来源。
只对已截断的最终结果集回表，行数小，两次批量查询开销可忽略。

MVP：定义抽象接口 + Noop 空实现；真实 DB 查询（t_knowledge_chunk / t_knowledge_document）
属 C 层 storage/database，届时注入实现替换即可，MetadataEnrichmentPostProcessor 面向本抽象编程。

对应 ragent 源码：
    com.nageoffer.ai.ragent.knowledge.service.impl.ChunkMetadataResolver
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ChunkMeta:
    """
    分块所属文档的元数据（对应 Java ChunkMetadataResolver.ChunkMeta record）

    Attributes:
        doc_id:      所属文档 ID
        chunk_index: 分块在所属文档中的序号，从 0 开始
        doc_name:    所属文档名称
    """

    doc_id: str
    chunk_index: Optional[int] = None
    doc_name: Optional[str] = None


class ChunkMetadataResolver(ABC):
    """分块元数据解析器接口（对应 Java ChunkMetadataResolver）"""

    @abstractmethod
    def resolve(self, chunk_ids: List[str]) -> Dict[str, ChunkMeta]:
        """
        批量解析分块元数据（对应 Java resolve）

        Args:
            chunk_ids: 检索命中的分块 ID 集合

        Returns:
            Dict[str, ChunkMeta]: chunkId → ChunkMeta；未命中的分块不出现在结果中
        """
        ...

    @abstractmethod
    def resolve_doc_names(self, doc_ids: List[str]) -> Dict[str, str]:
        """
        按 docId 批量解析文档标题（对应 Java resolveDocNames）

        供图谱等在 t_knowledge_chunk 无对应行、但已带归属 docId 的证据补真实文档标题。

        Args:
            doc_ids: 文档 ID 集合

        Returns:
            Dict[str, str]: docId → 文档标题；未命中的不出现在结果中
        """
        ...


class NoopChunkMetadataResolver(ChunkMetadataResolver):
    """
    空实现：不解析任何元数据（MVP 兜底 / 测试注入）

    所有输入均返回空映射，富化后 chunk 的 docId/chunkIndex/docName 保持不变。
    """

    def resolve(self, chunk_ids: List[str]) -> Dict[str, ChunkMeta]:
        return {}

    def resolve_doc_names(self, doc_ids: List[str]) -> Dict[str, str]:
        return {}