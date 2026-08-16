"""
rag.ingestion.splitter - 文本切分器包

    - base：ChunkBudget（分块预算）+ ChunkingService（分块入口，对应 Java）+ ChunkerDispatcher 抽象 + TextChunkDispatcher（MVP）
    - text_splitter：边界感知切分（换行 → CJK 标点 → 英文标点回溯）
"""
from rag.ingestion.splitter.base import (
    ChunkBudget,
    ChunkerDispatcher,
    ChunkingService,
    TextChunkDispatcher,
)
from rag.ingestion.splitter.text_splitter import TextSplitter

__all__ = [
    "ChunkBudget",
    "ChunkerDispatcher",
    "ChunkingService",
    "TextChunkDispatcher",
    "TextSplitter",
]
