"""
rag.source - 引用与来源组装

    - citation：行内引用标记清理 + 引用上下文注入（CitationMarkup + CitationContextEnricher）
    - assembler：文档级来源与 grounding 片段组装（SourcesAssembler + GroundingChunksAssembler）

对应 ragent 源码：
    - rag/core/source/CitationMarkup
    - rag/core/source/CitationContextEnricher
    - rag/core/source/SourcesAssembler
    - rag/core/source/GroundingChunksAssembler
"""
from rag.source.assembler import (
    DocumentInfo,
    DocumentMetadataProvider,
    GroundingChunksAssembler,
    SourcesAssembler,
)
from rag.source.citation import CitationContextEnricher, CitationMarkup

__all__ = [
    "CitationMarkup",
    "CitationContextEnricher",
    "SourcesAssembler",
    "GroundingChunksAssembler",
    "DocumentInfo",
    "DocumentMetadataProvider",
]
