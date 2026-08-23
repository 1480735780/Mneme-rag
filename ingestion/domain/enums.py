# -*- coding: utf-8 -*-
"""
ingestion.domain.enums - 摄取域枚举五件套（对应 Java ingestion/domain/enums/*）

    - IngestionStatus：   摄取任务状态（pending / running / failed / completed）
    - IngestionNodeType： 摄取节点类型（fetcher / parser / enhancer / chunker / enricher / indexer）
    - SourceType：        文档源类型（file / url / feishu）
    - EnhanceType：       整篇文档增强类型（context_enhance / keywords / questions / metadata）
    - ChunkEnrichType：   分块富集类型（keywords / summary / metadata）

对齐 Java：值一律小写 snake_case；`from_value` 宽松归一（trim+lower、`-`→`_`），
按 value 或枚举名匹配；None 输入返回 None、未知抛 ValueError（对应 Java
IllegalArgumentException）——Java 的 normalize 是私有辅助，Python 以模块级 `_normalize` 对应。

对应 ragent 源码：
    - ingestion/domain/enums/IngestionStatus / IngestionNodeType / SourceType / EnhanceType / ChunkEnrichType
"""
from __future__ import annotations

from enum import Enum


def _normalize(value: str) -> str:
    """归一化输入：trim + lower + `-`→`_`（对应 Java 各枚举私有的 normalize）"""
    return value.strip().lower().replace("-", "_")


class _StrictFromValueEnum(Enum):
    """共享 from_value 语义：None→None；未知抛 ValueError"""

    @classmethod
    def from_value(cls, value: str | None):
        if value is None:
            return None
        normalized = _normalize(value)
        for member in cls:
            if member.value == normalized or member.name.lower() == normalized:
                return member
        raise ValueError(f"Unknown {cls.__name__}: {value}")


class IngestionStatus(_StrictFromValueEnum):
    """摄取任务状态（对应 Java IngestionStatus；value 即落库 code）"""

    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"


class IngestionNodeType(_StrictFromValueEnum):
    """摄取节点类型（对应 Java IngestionNodeType；value 即落库 code）"""

    FETCHER = "fetcher"
    PARSER = "parser"
    ENHANCER = "enhancer"
    CHUNKER = "chunker"
    ENRICHER = "enricher"
    INDEXER = "indexer"


class SourceType(_StrictFromValueEnum):
    """文档源类型（对应 Java SourceType；value 即落库 code）"""

    FILE = "file"
    URL = "url"
    FEISHU = "feishu"


class EnhanceType(_StrictFromValueEnum):
    """整篇文档增强类型（对应 Java EnhanceType）"""

    CONTEXT_ENHANCE = "context_enhance"
    KEYWORDS = "keywords"
    QUESTIONS = "questions"
    METADATA = "metadata"


class ChunkEnrichType(_StrictFromValueEnum):
    """分块富集类型（对应 Java ChunkEnrichType）"""

    KEYWORDS = "keywords"
    SUMMARY = "summary"
    METADATA = "metadata"
