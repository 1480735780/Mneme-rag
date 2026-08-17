"""
文档来源组装器（对应 ragent SourcesAssembler + GroundingChunksAssembler）

职责划分：
    - SourcesAssembler：把检索片段（KB 命中）按文档去重、按相关度赋号，补齐来源类型与
      外部链接，产出文档级来源列表（SourceRef）。该列表既用于 SSE 下发/面板展示，
      也作为行内角标的唯一编号源。
    - GroundingChunksAssembler：把检索片段按文档去重、取最高分片段、限定条数，
      产出推荐问题 grounding 片段（GroundingChunk），随 assistant 消息落库。

两者职责分离：SourcesAssembler 面向来源面板/预览（摘录 100 字），
GroundingChunksAssembler 面向推荐生成 grounding（片段取全文、上限 8 条）。

Python 无 DAO 层，文档元数据（docName/sourceType/fileType/sourceLocation）通过
可注入的 DocumentMetadataProvider 补齐；不注入时视为无文档元数据，仅靠片段自带信息。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.source.SourcesAssembler
    - com.nageoffer.ai.ragent.rag.core.source.GroundingChunksAssembler
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.llm.schema import GroundingChunk, RetrievedChunk, SourceRef

# 摘录最大长度 超出以省略号截断
EXCERPT_MAX_LENGTH = 100

# grounding 片段条数上限 控制存储与 prompt 体积（文档已去重，8 篇足够支撑 3 条追问的发散）
MAX_GROUNDING_CHUNKS = 8

_SOURCE_TYPE_URL = "url"
_SOURCE_TYPE_FEISHU = "feishu"


@dataclass(frozen=True)
class DocumentInfo:
    """
    文档元数据（对应 ragent KnowledgeDocumentDO 的子集）

    由 DocumentMetadataProvider 按 docId 批量补齐，供来源装配填充展示字段。
    """

    doc_id: str
    doc_name: Optional[str] = None
    source_type: Optional[str] = None
    file_type: Optional[str] = None
    source_location: Optional[str] = None


class DocumentMetadataProvider(ABC):
    """文档元数据提供者（对应 Java KnowledgeDocumentMapper.selectBatchIds）"""

    @abstractmethod
    def get_docs(self, doc_ids: List[str]) -> Dict[str, DocumentInfo]:
        """
        按 docId 批量查询文档元数据

        Args:
            doc_ids: 文档 ID 列表

        Returns:
            Dict[str, DocumentInfo]: docId → 文档元数据，缺失的 docId 不出现在结果里
        """
        ...


class SourcesAssembler:
    """
    回答来源装配器（对应 Java SourcesAssembler）

    把检索片段（KB 命中）按文档去重、按相关度赋号，补齐来源类型与外部链接，
    产出文档级来源列表。该列表既用于 SSE 下发/面板展示，也作为行内角标的唯一编号源。
    """

    def __init__(self, metadata_provider: Optional[DocumentMetadataProvider] = None):
        self._metadata_provider = metadata_provider

    def assemble(self, intent_chunks: Dict[str, List[RetrievedChunk]]) -> List[SourceRef]:
        """
        由检索上下文的意图分片装配文档级来源列表

        Args:
            intent_chunks: 意图 ID → 命中片段（KB）

        Returns:
            List[SourceRef]: 文档级来源列表，无来源返回空列表
        """
        if not intent_chunks:
            return []

        # 按 docId 归并 保留最高分片段（作为摘录与排序依据）
        best_by_doc: Dict[str, RetrievedChunk] = {}
        for chunks in intent_chunks.values():
            if not chunks:
                continue
            for chunk in chunks:
                if chunk is None or not chunk.doc_id or not chunk.doc_id.strip():
                    continue
                existing = best_by_doc.get(chunk.doc_id)
                if existing is None or _score(chunk) > _score(existing):
                    best_by_doc[chunk.doc_id] = chunk
        if not best_by_doc:
            return []

        # 按最高分降序排列 同分按 docId 稳定排序；所有进入上下文的文档都必须能获得引用编号
        ordered = sorted(
            best_by_doc.values(),
            key=lambda c: (-_score(c), c.doc_id),
        )

        docs = self._load_docs([c.doc_id for c in ordered])

        sources: List[SourceRef] = []
        for index, chunk in enumerate(ordered, start=1):
            doc = docs.get(chunk.doc_id)
            source_type = doc.source_type if doc is not None else None
            sources.append(
                SourceRef(
                    index=index,
                    doc_id=chunk.doc_id,
                    doc_name=_resolve_doc_name(chunk, doc),
                    source_type=source_type,
                    file_type=doc.file_type if doc is not None else None,
                    url=_resolve_url(source_type, doc),
                    excerpt=_truncate_excerpt(chunk.text),
                )
            )
        return sources

    def _load_docs(self, doc_ids: List[str]) -> Dict[str, DocumentInfo]:
        """批量补齐文档元数据；未注入 provider 时视为无文档元数据"""
        if self._metadata_provider is None or not doc_ids:
            return {}
        return self._metadata_provider.get_docs(doc_ids)


class GroundingChunksAssembler:
    """
    推荐问题 grounding 片段装配器（对应 Java GroundingChunksAssembler）

    只负责选择：把检索片段（KB 命中）按文档去重、取最高分片段、限定条数。
    不设字符预算：片段最终只喂给推荐生成器，prompt 体积由消费方在模型调用边界统一控制。
    """

    def assemble(self, intent_chunks: Dict[str, List[RetrievedChunk]]) -> List[GroundingChunk]:
        """
        由检索上下文的意图分片装配 grounding 片段列表

        Args:
            intent_chunks: 意图 ID → 命中片段（KB）

        Returns:
            List[GroundingChunk]: grounding 片段列表，无命中返回空列表
        """
        if not intent_chunks:
            return []

        # 按 docId 归并 保留最高分片段（与 SourcesAssembler 同语义，保证文档多样性）
        best_by_doc: Dict[str, RetrievedChunk] = {}
        for chunks in intent_chunks.values():
            if not chunks:
                continue
            for chunk in chunks:
                if (
                    chunk is None
                    or not chunk.doc_id
                    or not chunk.doc_id.strip()
                    or not chunk.text
                    or not chunk.text.strip()
                ):
                    continue
                existing = best_by_doc.get(chunk.doc_id)
                if existing is None or _score(chunk) > _score(existing):
                    best_by_doc[chunk.doc_id] = chunk
        if not best_by_doc:
            return []

        # 按最高分降序取上限；文本取全文（预算交由消费方在模型调用边界统一控制）
        ordered = sorted(best_by_doc.values(), key=lambda c: -_score(c))[:MAX_GROUNDING_CHUNKS]
        return [
            GroundingChunk(
                doc_name=_blank_to_default(chunk.doc_name, chunk.doc_id),
                text=chunk.text.strip(),
            )
            for chunk in ordered
        ]


def _score(chunk: RetrievedChunk) -> float:
    """缺失分数按 0 参与排序（对应 Java score(chunk)）"""
    return chunk.score if chunk.score is not None else 0.0


def _resolve_doc_name(chunk: RetrievedChunk, doc: Optional[DocumentInfo]) -> Optional[str]:
    """文档名优先取片段自带，缺失回退文档元数据（对应 Java resolveDocName）"""
    if chunk.doc_name and chunk.doc_name.strip():
        return chunk.doc_name
    return doc.doc_name if doc is not None else None


def _resolve_url(source_type: Optional[str], doc: Optional[DocumentInfo]) -> Optional[str]:
    """
    外部原始链接：仅 url/feishu 来源携带，file 走 docId 预览提取正文

    对应 Java resolveUrl（StrUtil.blankToDefault(sourceLocation, null)）
    """
    if doc is None or source_type is None:
        return None
    if source_type.lower() == _SOURCE_TYPE_URL or source_type.lower() == _SOURCE_TYPE_FEISHU:
        return _blank_to_default(doc.source_location, None)
    return None


def _truncate_excerpt(text: Optional[str]) -> Optional[str]:
    """摘录截断：trim 后超长以省略号截断（对应 Java StrUtil.maxLength）"""
    if text is None:
        return None
    trimmed = text.strip()
    if len(trimmed) <= EXCERPT_MAX_LENGTH:
        return trimmed
    return trimmed[: EXCERPT_MAX_LENGTH - 3] + "..."


def _blank_to_default(value: Optional[str], default: Optional[str]) -> Optional[str]:
    """空白字符串回落默认值（对应 Java StrUtil.blankToDefault）"""
    if value is not None and value.strip():
        return value
    return default
