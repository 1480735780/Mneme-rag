# -*- coding: utf-8 -*-
"""rag.ingestion.splitter.blockaware.paragraph_chunker - 段落分块器（对应 Java ParagraphChunker）

优先整段保留，超出容忍上限才按块大小降级切分。
降级切分走语义路径（SemanticTextSplitter）：先切句再按预算攒整句，切口恒在句末；
回退到 TextSplitter 只发生在整段无句界可切时。之所以不再直接委托 TextSplitter 硬切：
它的边界回溯距离是常数（overlap），长句会让回溯落空、切在句子中间，评测里的 bad_cut_rate 正是这一项。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.ParagraphChunker
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import ParagraphBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import (
    STRATEGY_BLOCKAWARE,
    STRATEGY_SEMANTIC,
    ChunkDraft,
)
from rag.ingestion.splitter.semantic_text_splitter import SemanticTextSplitter
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
        strategy = STRATEGY_BLOCKAWARE
        if len(pieces) > 1:
            pieces = SemanticTextSplitter.split(block.text, ctx.budget.max_chars, overlap)
            strategy = STRATEGY_SEMANTIC
        if not pieces:
            return []

        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
        )
        spans = self._locate_spans(block.text, pieces, overlap)
        result = [
            ChunkDraft(content=piece, metadata=metadata, source_span=span, strategy=strategy)
            for piece, span in zip(pieces, spans)
        ]
        return ChunkDraft.pieces(result)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _locate_spans(
        source: str, pieces: Sequence[str], overlap: int
    ) -> List[Optional[Tuple[int, int]]]:
        """回填各片在原文中的 [起, 止) 区间（对应方案 3.2.3 source_span）

        TextSplitter / SemanticTextSplitter 只回片文本、不回偏移，所以只能在原文里反查。
        查的基准必须是归一化文本：切出来的片本身就是归一化文本的切片（软换行已合并、\\r 已去），
        拿未归一化的 block.text 去 find 会因这几处差异找不到。

        起点从「上一片末尾往前 overlap 字」起找：相邻片正是靠这截重叠尾巴衔接的，
        该位置就是本片的真实起点，find 命中的就是它。

        定位不到给 None，而不是回落成某个近似区间：语义切分器在没有句界可切时会把整段交回
        TextSplitter（片之间已带重叠），外层再加一次重叠尾巴，这样的片就不是原文的连续切片，
        本就不存在能表示它的单一区间；span 只用于排障与父块定位，给个错的比留空更害人。
        """
        if not pieces:
            return []
        normalized = TextSplitter.normalize(source)
        spans: List[Optional[Tuple[int, int]]] = []
        cursor = 0
        for piece in pieces:
            start = normalized.find(piece, cursor)
            if start < 0:
                spans.append(None)
                continue
            end = start + len(piece)
            spans.append((start, end))
            cursor = max(0, end - overlap)
        return spans
