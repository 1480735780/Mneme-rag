# -*- coding: utf-8 -*-
"""
ingestion.node.indexer_node - 索引节点（对应 Java IndexerNode）

把已向量化的块写入向量存储：
    - 分区解析：vector_target.partition → vector_space_id.logical_name → 默认集合名
    - 管道级元数据写进块 extras（task_id/pipeline_id/source_type/source_location + context.metadata），
      metadataFields 非空则按白名单收窄
    - skipIndexerWrite=True 时只做准备不写向量（调用方事务内统一落库）
    - 写前确保向量空间存在

对应 ragent 源码：
    - ingestion/node/IndexerNode
"""
from __future__ import annotations

import logging
from typing import List, Optional

from common.exception.business import ClientException
from core.llm.schema import EmbeddedChunk
from ingestion.domain.context import IngestionContext
from ingestion.domain.enums import IngestionNodeType
from ingestion.domain.pipeline import NodeConfig
from ingestion.domain.result import NodeResult
from ingestion.domain.settings import IndexerSettings
from ingestion.node.base import IngestionNode
from rag.retrieval.vector_store import VectorStoreAdmin, VectorStoreService
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec

logger = logging.getLogger(__name__)


class IndexerNode(IngestionNode):
    """索引节点（对齐 Java IndexerNode）"""

    def __init__(
        self,
        vector_store: VectorStoreService,
        vector_store_admin: VectorStoreAdmin,
        default_collection_name: Optional[str] = None,
    ):
        self._vector_store = vector_store
        self._admin = vector_store_admin
        self._default_collection_name = default_collection_name

    def get_node_type(self) -> str:
        return IngestionNodeType.INDEXER.value

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        chunks = context.chunks or []
        if not chunks:
            return NodeResult.fail(ClientException("没有可索引的分块"))
        settings = _parse_settings(config.settings)
        partition = self._resolve_partition(context)
        if not partition:
            return NodeResult.fail(ClientException("索引器需要指定集合名称"))

        enriched = _attach_pipeline_metadata(context, chunks, settings.metadata_fields)
        context.chunks = enriched

        if context.skip_indexer_write:
            return NodeResult.ok(
                f"已准备 {len(enriched)} 个分块（向量写入由调用方统一完成）"
            )

        self._ensure_vector_space(partition)
        await self._vector_store.index_document_chunks(partition, context.task_id, enriched)
        logger.info("向量写入成功，集合=%s，行数=%s", partition, len(enriched))
        return NodeResult.ok(f"已写入 {len(enriched)} 个分块到集合 {partition}")

    def _resolve_partition(self, context: IngestionContext) -> Optional[str]:
        if context.vector_target is not None and context.vector_target.partition:
            return context.vector_target.partition
        if context.vector_space_id is not None and context.vector_space_id.logical_name:
            return context.vector_space_id.logical_name
        return self._default_collection_name

    def _ensure_vector_space(self, partition: str) -> None:
        space_id = VectorSpaceId(logical_name=partition)
        if self._admin.vector_space_exists(space_id):
            return
        self._admin.ensure_vector_space(VectorSpaceSpec(space_id=space_id, remark="RAG向量存储空间"))


def _parse_settings(raw: Optional[dict]) -> IndexerSettings:
    if not raw:
        return IndexerSettings()
    return IndexerSettings(
        embedding_model=raw.get("embeddingModel"),
        metadata_fields=list(raw.get("metadataFields") or []),
    )


def _put_if_present(target: dict, key: str, value: Optional[str]) -> None:
    if value:
        target[key] = value


def _attach_pipeline_metadata(context: IngestionContext, chunks: List[EmbeddedChunk],
                              metadata_fields: Optional[List[str]]) -> List[EmbeddedChunk]:
    """把管道级信息写进块元数据扩展位（对齐 Java attachPipelineMetadata）"""
    pipeline_metadata: dict = {}
    _put_if_present(pipeline_metadata, "task_id", context.task_id)
    _put_if_present(pipeline_metadata, "pipeline_id", context.pipeline_id)
    source = context.source
    if source is not None:
        if source.type is not None:
            pipeline_metadata["source_type"] = source.type.value
        _put_if_present(pipeline_metadata, "source_location", source.location)
    if context.metadata:
        pipeline_metadata.update(context.metadata)
    if metadata_fields:
        pipeline_metadata = {k: v for k, v in pipeline_metadata.items() if k in metadata_fields}
    if not pipeline_metadata:
        return chunks

    result = []
    for chunk in chunks:
        result.append(EmbeddedChunk(
            chunk=chunk.chunk.with_metadata(chunk.metadata.with_extras(pipeline_metadata)),
            embedding=chunk.embedding,
        ))
    return result
