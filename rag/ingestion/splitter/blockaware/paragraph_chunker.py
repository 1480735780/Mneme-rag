# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.paragraph_chunker - 段落分块器（对应 Java ParagraphChunker）

优先整段保留，超出容忍上限才按块大小降级切分。
切分一律委托 TextSplitter，由它做边界回溯（换行 / 中文句末 / 英文句末）与文本归一化
（URL 断行修复、CJK 软换行合并），本类不自行按下标截断。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.ParagraphChunker
"""
from __future__ import annotations

from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import ParagraphBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft
from rag.ingestion.splitter.text_splitter import TextSplitter


class ParagraphChunker(BlockChunker[ParagraphBlock]):
    """段落 chunker：优先整段保留，超出容忍上限才按块大小降级切分（对应 Java ParagraphChunker）"""

    def block_type(self) -> type:
        return ParagraphBlock

    def chunk(self, block: Optional[ParagraphBlock], ctx: ChunkContext) -> List[ChunkDraft]:
        if block is None:
            return []
        overlap = ctx.budget.overlap_chars
        # 先按容忍上限量一次，切不动说明整段撑得住；量出多片才退回块大小重切
        pieces = TextSplitter.split(block.text, ctx.budget.tolerance_chars(), overlap)
        if len(pieces) > 1:
            pieces = TextSplitter.split(block.text, ctx.budget.max_chars, overlap)
        if not pieces:
            return []

        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
        )
        result = [ChunkDraft.of(piece, metadata) for piece in pieces]
        return ChunkDraft.pieces(result)
