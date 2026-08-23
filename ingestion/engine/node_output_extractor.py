# -*- coding: utf-8 -*-
"""
ingestion.engine.node_output_extractor - 节点输出提取器（对应 Java NodeOutputExtractor）

从 IngestionContext 取各节点输出的**摘要**（落 t_ingestion_task_node.output_json，1MB 截断、
纯诊断用途）：只产摘要不产实体——源文件字节 / 全量 Block / 带向量的块必然顶穿阈值，
实体各有正本（对象存储 / t_knowledge_chunk / 向量库）。

按节点类型分派：fetcher（source/mimeType/rawBytesLength）、parser（mimeType/rawText/
blockCount/blockTypes）、enhancer（enhancedText/keywords/questions/metadata）、
chunker（chunkSummary）、enricher（chunkSummary + extraKeys）、indexer（settings + chunkSummary）。

对应 ragent 源码：
    - ingestion/engine/NodeOutputExtractor
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ingestion.domain.context import IngestionContext
from ingestion.domain.enums import IngestionNodeType
from ingestion.domain.pipeline import NodeConfig


def _count_by_type(blocks: List[Any]) -> Dict[str, int]:
    """按 Block 具体类名计数（对应 Java countByType 的 getClass().getSimpleName()）"""
    counts: Dict[str, int] = {}
    for block in blocks:
        name = block.__class__.__name__
        counts[name] = counts.get(name, 0) + 1
    return counts


def _safe_chunks(context: IngestionContext) -> List[Any]:
    """空/含 None 保护（提取器在节点失败分支也会被调用，那一次调用不在 try 内）"""
    return [c for c in (context.chunks or []) if c is not None]


def _chunk_summary(chunks: List[Any]) -> Dict[str, Any]:
    """分块/加工/索引三节点共用的块摘要（不放整块与向量）"""
    total_chars = sum(len(c.content or "") for c in chunks)
    first_dim = 0 if not chunks else getattr(chunks[0], "dimension", 0) or 0
    return {"chunkCount": len(chunks), "totalChars": total_chars, "embeddingDim": first_dim}


def _collect_extra_keys(chunks: List[Any]) -> set:
    """汇总各块 extras 键（对齐 Java collectExtraKeys）"""
    keys: set = set()
    for chunk in chunks:
        metadata = getattr(chunk, "metadata", None)
        extras = getattr(metadata, "extras", None) if metadata is not None else None
        if isinstance(extras, dict):
            keys.update(extras.keys())
    return keys


class NodeOutputExtractor:
    """节点输出提取器（对应 Java NodeOutputExtractor）"""

    def extract(self, context: Optional[IngestionContext],
                config: Optional[NodeConfig]) -> Dict[str, Any]:
        if context is None or config is None:
            return {}
        node_type = self._resolve_node_type(config.node_type)
        if node_type is None:
            return self._generic_output(context)
        if node_type is IngestionNodeType.FETCHER:
            return self._fetcher_output(context)
        if node_type is IngestionNodeType.PARSER:
            return self._parser_output(context)
        if node_type is IngestionNodeType.ENHANCER:
            return self._enhancer_output(context)
        if node_type is IngestionNodeType.CHUNKER:
            return _chunk_summary(_safe_chunks(context))
        if node_type is IngestionNodeType.ENRICHER:
            return self._enricher_output(context)
        return self._indexer_output(context, config)

    def _fetcher_output(self, context: IngestionContext) -> Dict[str, Any]:
        output: Dict[str, Any] = {}
        source = context.source
        if source is not None:
            output["source"] = {
                "type": source.type.value if source.type is not None else None,
                "location": source.location,
                "fileName": source.file_name,
            }
        output["mimeType"] = context.mime_type
        if context.raw_bytes is not None:
            # 只记长度不记内容：源文件 base64 约 1.33 倍体积，塞实体必顶穿截断
            output["rawBytesLength"] = len(context.raw_bytes)
        return output

    def _parser_output(self, context: IngestionContext) -> Dict[str, Any]:
        blocks = (context.document.blocks or []) if context.document is not None else []
        return {
            "mimeType": context.mime_type,
            "rawText": context.raw_text,
            "blockCount": len(blocks),
            "blockTypes": _count_by_type(blocks),
        }

    def _enhancer_output(self, context: IngestionContext) -> Dict[str, Any]:
        return {
            "enhancedText": context.enhanced_text,
            "keywords": context.keywords,
            "questions": context.questions,
            "metadata": context.metadata,
        }

    def _enricher_output(self, context: IngestionContext) -> Dict[str, Any]:
        chunks = _safe_chunks(context)
        output = _chunk_summary(chunks)
        output["extraKeys"] = sorted(_collect_extra_keys(chunks))
        return output

    def _indexer_output(self, context: IngestionContext, config: NodeConfig) -> Dict[str, Any]:
        output: Dict[str, Any] = {"settings": config.settings}
        output.update(_chunk_summary(_safe_chunks(context)))
        return output

    def _generic_output(self, context: IngestionContext) -> Dict[str, Any]:
        output: Dict[str, Any] = {
            "mimeType": context.mime_type,
            "rawText": context.raw_text,
            "enhancedText": context.enhanced_text,
            "keywords": context.keywords,
            "questions": context.questions,
            "metadata": context.metadata,
        }
        output.update(_chunk_summary(_safe_chunks(context)))
        return output

    @staticmethod
    def _resolve_node_type(node_type: Optional[str]) -> Optional[IngestionNodeType]:
        if not node_type or not node_type.strip():
            return None
        try:
            return IngestionNodeType.from_value(node_type)
        except ValueError:
            return None
