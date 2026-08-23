# -*- coding: utf-8 -*-
"""
ingestion.node.fetcher_node - 文档获取节点（对应 Java FetcherNode）

策略模式：按 source.type 路由到 DocumentFetcher；幂等：上下文中已预置原始字节则跳过
（只补 MIME，不重复 I/O）。

对应 ragent 源码：
    - ingestion/node/FetcherNode
"""
from __future__ import annotations

from typing import Dict, List

from common.exception.business import ClientException
from ingestion.domain.context import IngestionContext
from ingestion.domain.enums import IngestionNodeType
from ingestion.domain.pipeline import NodeConfig
from ingestion.domain.result import NodeResult
from ingestion.node.base import IngestionNode
from ingestion.strategy.fetcher.base import DocumentFetcher
from rag.ingestion.kernel import MimeTypeDetector


class FetcherNode(IngestionNode):
    """文档获取节点（对齐 Java FetcherNode）"""

    def __init__(self, fetchers: List[DocumentFetcher]):
        self._fetchers: Dict[str, DocumentFetcher] = {}
        for fetcher in fetchers:
            self._fetchers[fetcher.supported_type().value] = fetcher

    def get_node_type(self) -> str:
        return IngestionNodeType.FETCHER.value

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        if context.raw_bytes is not None and len(context.raw_bytes) > 0:
            if not context.mime_type:
                file_name = context.source.file_name if context.source is not None else None
                context.mime_type = MimeTypeDetector.detect(context.raw_bytes, file_name)
            return NodeResult.ok("已跳过获取器：原始字节已存在")

        source = context.source
        if source is None or source.type is None:
            return NodeResult.fail(ClientException("文档来源不能为空"))

        fetcher = self._fetchers.get(source.type.value)
        if fetcher is None:
            return NodeResult.fail(ClientException(f"不支持的来源类型: {source.type.value}"))

        result = await fetcher.fetch(source)
        context.raw_bytes = result.content
        if result.mime_type:
            context.mime_type = result.mime_type
        if result.file_name:
            source.file_name = result.file_name
        return NodeResult.ok(f"已获取 {len(result.content) if result.content else 0} 字节")
