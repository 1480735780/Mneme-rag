# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.html_table_chunker - HTML 表格分块器（对应 Java HtmlTableChunker）

按 tr 边界切分，每块重复表头行并包回完整 table。
不转成管道表：合并单元格与单元格内的换行在展开成二维表时会失真，展示与检索都用同一份 HTML。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.HtmlTableChunker
"""
from __future__ import annotations

import re
from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import HtmlTableBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft

_ROW_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)

# 值为 1 的 colspan / rowspan 不表达任何合并，MinerU 却逐格都写，一张十来行的表能被它撑到三倍
_NO_OP_SPAN_RE = re.compile(r'\s+(?:colspan|rowspan)\s*=\s*["\']?1["\']?', re.IGNORECASE)

_TABLE_CLOSE = "</table>"


class HtmlTableChunker(BlockChunker[HtmlTableBlock]):
    """HTML 表格 chunker：按 tr 边界切分，每块重复表头并包回完整 table（对应 Java HtmlTableChunker）"""

    def block_type(self) -> type:
        return HtmlTableBlock

    def chunk(self, block: Optional[HtmlTableBlock], ctx: ChunkContext) -> List[ChunkDraft]:
        if block is None or not (block.html or "").strip():
            return []
        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
        )

        html = _NO_OP_SPAN_RE.sub("", block.html)
        rows = self._split_rows(html)
        # 只有表头或压根扫不出行：没有可切的边界，原样落块，宁可超预算也不切在标签中间
        if len(rows) < 2:
            return [ChunkDraft.of(html, metadata)]

        open_tag = self._open_tag(html)
        header = rows[0]
        max_rows = max(1, ctx.budget.rows_per_chunk)
        # 整张表撑得住容忍上限就不切，切开后每块虽重带表头，跨块的行间对比仍然做不了
        if len(rows) - 1 <= max_rows and len(html) <= ctx.budget.tolerance_chars():
            budget = ctx.budget.tolerance_chars()
        else:
            budget = max(1, ctx.budget.max_chars)
        # 外壳与表头每块都要重复，先从预算里扣掉，否则渲染出来必然超
        overhead = len(open_tag) + len(_TABLE_CLOSE) + len(header)

        result: List[ChunkDraft] = []
        group: List[str] = []
        group_len = 0
        for row in rows[1:]:
            over_cap = len(group) >= max_rows
            over_budget = bool(group) and overhead + group_len + len(row) > budget
            if over_cap or over_budget:
                result.append(ChunkDraft.of(self._render(open_tag, header, group), metadata))
                group = []
                group_len = 0
            group.append(row)
            group_len += len(row)
        result.append(ChunkDraft.of(self._render(open_tag, header, group), metadata))
        return ChunkDraft.pieces(result)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_rows(html: str) -> List[str]:
        """切出各 tr 片段（含标签本身），首个即表头行"""
        return _ROW_RE.findall(html)

    @staticmethod
    def _open_tag(html: str) -> str:
        """取原始 table 开标签而非写死，作者写在上面的 border / class 等属性得跟着每一块走"""
        end = html.find(">")
        return html[: end + 1] if end >= 0 else "<table>"

    @staticmethod
    def _render(open_tag: str, header: str, rows: List[str]) -> str:
        return open_tag + header + "".join(rows) + _TABLE_CLOSE
