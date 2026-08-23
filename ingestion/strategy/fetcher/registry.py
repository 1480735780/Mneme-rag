# -*- coding: utf-8 -*-
"""
ingestion.strategy.fetcher.registry - 文档拉取策略注册表

按 SourceType 汇聚所有 DocumentFetcher（对齐 Java FetcherNode 构造时
`Collectors.toMap(DocumentFetcher::supportedType, identity)` 的映射）；
FetcherNode 依 context.source.type 路由取用。

对应 ragent 源码：
    - ingestion/node/FetcherNode（fetchers 映射的构建部分）
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ingestion.domain.enums import SourceType
from ingestion.strategy.fetcher.base import DocumentFetcher


class DocumentFetcherRegistry:
    """fetcher 注册表：source_type → fetcher（后注册覆盖先注册，与 Java toMap 语义一致）"""

    def __init__(self, fetchers: List[DocumentFetcher]):
        self._fetchers: Dict[SourceType, DocumentFetcher] = {}
        for fetcher in fetchers:
            self._fetchers[fetcher.supported_type()] = fetcher

    def get(self, source_type: Optional[SourceType]) -> Optional[DocumentFetcher]:
        """按源类型取对应 fetcher；未注册返回 None"""
        if source_type is None:
            return None
        return self._fetchers.get(source_type)
