# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.list_chunker - 列表分块器（对应 Java ListChunker）

按渲染后的字符体量把列表项贪心分组，绝不从项中间切断。
度量取字符而非项数：项数是体量的坏代理，十几条词条的清单合起来两百来字，按项数切开后打包器又
原样并回来，只在正文里留下一串多余空行。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.ListChunker
"""
from __future__ import annotations

from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import ListBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft


class ListChunker(BlockChunker[ListBlock]):
    """列表 chunker：按渲染体量贪心分组，绝不从项中间切断（对应 Java ListChunker）"""

    def block_type(self) -> type:
        return ListBlock

    def chunk(self, block: Optional[ListBlock], ctx: ChunkContext) -> List[ChunkDraft]:
        if block is None or not block.items:
            return []
        items = list(block.items)
        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
        )

        # 整份清单撑得住容忍上限就不切，切开后「要交哪些材料」这类问题只能召回半份
        if self.rendered_length(block) <= ctx.budget.tolerance_chars():
            budget = ctx.budget.tolerance_chars()
        else:
            budget = max(1, ctx.budget.max_chars)

        result: List[ChunkDraft] = []
        start = 0
        cost = 0
        for i in range(len(items)):
            # 加一算项间换行；单项自身超预算时独立成块，硬切只会把词条腰斩
            item_cost = len(self.render_item(block, i + 1, items[i])) + 1
            if i > start and cost + item_cost > budget:
                result.append(self._build_draft(items[start:i], start + 1, block, metadata))
                start = i
                cost = 0
            cost += item_cost
        result.append(self._build_draft(items[start:], start + 1, block, metadata))
        return ChunkDraft.pieces(result)

    # ------------------------------------------------------------------ #

    def _build_draft(self, items: List[str], start_number: int, block: ListBlock, metadata: ChunkMetadata) -> ChunkDraft:
        """start_number 仅对有序列表生效，作为本块的起始编号"""
        lines = [self.render_item(block, start_number + i, item) for i, item in enumerate(items)]
        return ChunkDraft.of("\n".join(lines), metadata)

    @staticmethod
    def rendered_length(block: ListBlock) -> int:
        """整份清单渲染后的体量，含项间换行，用于判断切不切"""
        total = 0
        for i, item in enumerate(block.items):
            total += len(ListChunker.render_item(block, i + 1, item)) + 1
        return total

    @staticmethod
    def render_item(block: ListBlock, number: int, item: str) -> str:
        """单项渲染，同时用作预算切分的体量度量"""
        return f"{number}. {item}" if block.ordered else f"- {item}"
