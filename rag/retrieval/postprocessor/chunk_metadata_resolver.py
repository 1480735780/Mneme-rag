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

from storage.database import Condition, DatabaseClient


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


class DatabaseChunkMetadataResolver(ChunkMetadataResolver):
    """
    关系库实现：查 t_knowledge_chunk / t_knowledge_document 回表补齐分块元数据

    对齐 Java ChunkMetadataResolver 的完整语义：
        - resolve：按 chunkId 批量回表（去空、去重、只取 deleted=0），再按 docId 回表补文档标题；
        - resolve_doc_names：按 docId 批量回表取文档标题（只取 id 与 doc_name 均非 null 的命中）。
    两次批量查询都只针对已截断的最终结果集，行数小、开销可忽略。

    deleted=0 / 空白 span 过滤均对齐 Java：@TableLogic 自动附加 deleted=0；
    docId 空白被过滤、不参与第二次回表（docName 对齐 Java docNameById.get 的 null 兜底）。

    面向 DatabaseClient 抽象编程，注入 InMemoryDatabaseClient（测试 / MVP）或
    SqlDatabaseClient（真实 SQL）均无感知；MetadataEnrichmentPostProcessor 面向
    ChunkMetadataResolver 抽象编程，注入本实现即可从 Noop 切到真实回表。
    """

    CHUNK_TABLE = "t_knowledge_chunk"
    DOCUMENT_TABLE = "t_knowledge_document"

    def __init__(self, db: DatabaseClient):
        self._db = db

    def resolve(self, chunk_ids: List[str]) -> Dict[str, ChunkMeta]:
        distinct_ids = _non_blank_deduped(chunk_ids)
        if not distinct_ids:
            return {}
        chunks = self._db.select_rows(
            self.CHUNK_TABLE,
            columns=["id", "doc_id", "chunk_index"],
            where=[
                Condition.in_("id", distinct_ids),
                Condition.eq("deleted", 0),
            ],
        )
        if not chunks:
            return {}
        doc_name_by_id = self._resolve_doc_names_rows(
            [chunk.get("doc_id") for chunk in chunks]
        )
        result: Dict[str, ChunkMeta] = {}
        for chunk in chunks:
            chunk_id = chunk.get("id")
            if chunk_id is None:
                continue
            result[chunk_id] = ChunkMeta(
                doc_id=chunk.get("doc_id"),
                chunk_index=chunk.get("chunk_index"),
                doc_name=doc_name_by_id.get(chunk.get("doc_id")),
            )
        return result

    def resolve_doc_names(self, doc_ids: List[str]) -> Dict[str, str]:
        distinct_ids = _non_blank_deduped(doc_ids)
        if not distinct_ids:
            return {}
        return self._resolve_doc_names_rows(distinct_ids)

    def _resolve_doc_names_rows(self, doc_ids: List[str]) -> Dict[str, str]:
        distinct_ids = _non_blank_deduped(doc_ids)
        if not distinct_ids:
            return {}
        rows = self._db.select_rows(
            self.DOCUMENT_TABLE,
            columns=["id", "doc_name"],
            where=[
                Condition.in_("id", distinct_ids),
                Condition.eq("deleted", 0),
            ],
        )
        result: Dict[str, str] = {}
        for row in rows:
            doc_id = row.get("id")
            doc_name = row.get("doc_name")
            if doc_id is None or doc_name is None:
                continue  # 对齐 Java：doc.getId()!=null && docName()!=null
            result[doc_id] = doc_name
        return result


def _non_blank_deduped(values: List[Optional[str]]) -> List[str]:
    """过滤空 / 空白字符串 + 去重保序（对应 Java distinctIds 过滤 null/blank）"""
    seen = set()
    result: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result