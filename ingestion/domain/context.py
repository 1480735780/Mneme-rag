# -*- coding: utf-8 -*-
"""
ingestion.domain.context - 摄取上下文与节点日志（对应 Java ingestion/domain/context/*）

    - IngestionContext：管道执行全程承载/传递中间数据与状态
      （raw_bytes → structured_document → chunks → embedded 落库；含 vectorTarget 显式下发）
    - NodeLog：单节点执行日志（nodeId/nodeType/message/durationMs/success/error/output）
    - DocumentSource：文档源（type/location/fileName/credentials）
    - StructuredDocument：结构化文档（text/sections/tables/metadata/blocks）

对齐 Java：字段名即 Java 驼峰拆 snake_case；`chunks` 用 EmbeddedChunk（与 core/llm/schema 一致），
`vector_target` 用 VectorTarget（partition/embedding_model/dimension），`vector_space_id` 用
VectorSpaceId；`skip_indexer_write` 与 `assets` 为 @Builder.Default 语义（默认 False / 空列表）。

对应 ragent 源码：
    - ingestion/domain/context/IngestionContext / NodeLog / DocumentSource / StructuredDocument
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ingestion.domain.enums import IngestionStatus, SourceType

if TYPE_CHECKING:  # 类型注解仅供 IDE/静态检查，避免 domain 层耦合具体实现模块
    from core.llm.schema import EmbeddedChunk
    from rag.ingestion.parser.model import AssetRef, Block
    from storage.vector.schema import VectorSpaceId, VectorTarget


@dataclass
class DocumentSource:
    """文档源（对应 Java DocumentSource）"""

    type: Optional[SourceType] = None
    location: Optional[str] = None
    file_name: Optional[str] = None
    credentials: Optional[Dict[str, str]] = None


@dataclass
class StructuredSection:
    """文档章节（对应 Java StructuredDocument.StructuredSection）"""

    title: str = ""
    level: Optional[int] = None
    content: str = ""
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None


@dataclass
class StructuredTable:
    """文档表格（对应 Java StructuredDocument.StructuredTable）"""

    title: str = ""
    rows: List[List[str]] = field(default_factory=list)
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None


@dataclass
class StructuredDocument:
    """结构化文档（对应 Java StructuredDocument；blocks 为新链路首选输入）"""

    text: str = ""
    sections: List[StructuredSection] = field(default_factory=list)
    tables: List[StructuredTable] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    blocks: Optional[List["Block"]] = None


@dataclass
class NodeLog:
    """节点执行日志（对应 Java NodeLog）"""

    node_id: str
    node_type: str
    message: Optional[str] = None
    duration_ms: int = 0
    success: bool = False
    error: Optional[str] = None
    output: Optional[Dict[str, Any]] = None


@dataclass
class IngestionContext:
    """文档摄取上下文（对应 Java IngestionContext）"""

    task_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    source: Optional[DocumentSource] = None
    raw_bytes: Optional[bytes] = None
    mime_type: Optional[str] = None
    raw_text: Optional[str] = None
    document: Optional[StructuredDocument] = None
    chunks: Optional[List["EmbeddedChunk"]] = None
    vector_target: Optional["VectorTarget"] = None
    enhanced_text: Optional[str] = None
    keywords: Optional[List[str]] = None
    questions: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    vector_space_id: Optional["VectorSpaceId"] = None
    status: Optional[IngestionStatus] = None
    logs: List[NodeLog] = field(default_factory=list)
    error: Optional[BaseException] = None
    skip_indexer_write: bool = False
    assets: List["AssetRef"] = field(default_factory=list)
