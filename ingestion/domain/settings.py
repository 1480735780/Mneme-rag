# -*- coding: utf-8 -*-
"""
ingestion.domain.settings - 节点配置 settings 模型（对应 Java ingestion/domain/settings/*）

每类节点按自身类型携带独立 settings（落库于 t_ingestion_pipeline_node.settings_json）：
    - ChunkerSettings：分块预算（strategy 已废弃仅保留；chunk_size/overlap_size/separator/rows_per_chunk）
    - ParserSettings：解析规则（rules：按 mimeType 匹配解析器 + options）
    - EnhancerSettings：整篇文档增强（modelId + 任务列表 type/systemPrompt/userPromptTemplate）
    - EnricherSettings：分块富集（modelId + attachDocumentMetadata + 任务列表）
    - IndexerSettings：向量索引（embeddingModel + 待存储元数据字段列表）

对应 ragent 源码：
    - ingestion/domain/settings/{Chunker,Parser,Enhancer,Enricher,Indexer}Settings
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ingestion.domain.enums import ChunkEnrichType, EnhanceType


@dataclass
class ChunkerSettings:
    """分块器设置（对应 Java ChunkerSettings）"""

    strategy: Optional[str] = None  # 已废弃：保留仅为管道编辑器 UI 兼容，后端不读取
    chunk_size: Optional[int] = None
    overlap_size: Optional[int] = None
    separator: Optional[str] = None
    rows_per_chunk: Optional[int] = None  # block-aware TableChunker 行硬上限，空时由 ChunkerNode 取默认


@dataclass
class ParserSettings:
    """解析器设置（对应 Java ParserSettings）"""

    @dataclass
    class ParserRule:
        """单条解析规则（对应 Java ParserSettings.ParserRule）"""

        mime_type: Optional[str] = None
        options: Optional[Dict[str, Any]] = None

    rules: List[ParserRule] = field(default_factory=list)


@dataclass
class EnhancerSettings:
    """增强器设置（对应 Java EnhancerSettings）"""

    @dataclass
    class EnhanceTask:
        """单条增强任务（对应 Java EnhancerSettings.EnhanceTask）"""

        type: Optional[EnhanceType] = None
        system_prompt: Optional[str] = None
        user_prompt_template: Optional[str] = None

    model_id: Optional[str] = None
    tasks: List[EnhanceTask] = field(default_factory=list)


@dataclass
class EnricherSettings:
    """富集器设置（对应 Java EnricherSettings）"""

    @dataclass
    class ChunkEnrichTask:
        """单条分块富集任务（对应 Java EnricherSettings.ChunkEnrichTask）"""

        type: Optional[ChunkEnrichType] = None
        system_prompt: Optional[str] = None
        user_prompt_template: Optional[str] = None

    model_id: Optional[str] = None
    attach_document_metadata: Optional[bool] = None
    tasks: List[ChunkEnrichTask] = field(default_factory=list)


@dataclass
class IndexerSettings:
    """索引器设置（对应 Java IndexerSettings）"""

    embedding_model: Optional[str] = None
    metadata_fields: List[str] = field(default_factory=list)
