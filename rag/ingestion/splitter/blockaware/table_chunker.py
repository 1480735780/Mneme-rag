# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.table_chunker - 表格分块器（对应 Java TableChunker）

按 key-value 渲染长度累加到预算，每块都带完整表头，单行超预算时整行原子成块。
rows_per_chunk 只作硬上限，兼顾宽表不超嵌入上限、窄表不过度碎片化；展示文本是完整 markdown 表格，
向量文本改用「列名: 值」，因为 markdown 表格靠位置对齐列名与值、嵌入模型读不懂位置；表头不拼进
向量文本，KV 正文已逐格自带列名，重复前缀会把同一张表各块的向量朝同一方向拉、压缩块间距离，
表身份由 sheet 名经章节路径承载。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.TableChunker
"""
from __future__ import annotations

from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import TableBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft


class TableChunker(BlockChunker[TableBlock]):
    """表格 chunker：按 key-value 渲染长度累加到预算，每块带完整表头（对应 Java TableChunker）"""

    def block_type(self) -> type:
        return TableBlock

    def chunk(self, block: Optional[TableBlock], ctx: ChunkContext) -> List[ChunkDraft]:
        if block is None:
            return []
        headers = list(block.headers) if block.headers else []
        rows = list(block.rows) if block.rows else []
        if not headers and not rows:
            return []

        # 预算只量 KV 行本身，刻意不扣装配器追加的章节路径前缀：真去扣，深层章节会把可用预算逼近 0，
        # 退化成每行一块、每块大半是逐字相同的前缀
        max_rows = max(1, ctx.budget.rows_per_chunk)
        # 整张表撑得住容忍上限就不切，切开后每块虽重带表头，跨块的行间对比仍然做不了
        if len(rows) <= max_rows and len(self.render_key_value_rows(headers, rows)) <= ctx.budget.tolerance_chars():
            budget = ctx.budget.tolerance_chars()
        else:
            budget = max(1, ctx.budget.max_chars)

        result: List[ChunkDraft] = []

        if not rows:
            result.append(self._build_draft(block, ctx, headers, []))
            return result

        # 贪心累加：超硬上限或（非空且加入下一行会超预算）则先落块
        group: List[List[str]] = []
        group_cost = 0
        for row in rows:
            row_cost = len(self.render_key_value_row(headers, row))
            over_cap = len(group) >= max_rows
            over_budget = bool(group) and group_cost + row_cost > budget
            if over_cap or over_budget:
                result.append(self._build_draft(block, ctx, headers, group))
                group = []
                group_cost = 0
            group.append(row)
            group_cost += row_cost
        result.append(self._build_draft(block, ctx, headers, group))
        return ChunkDraft.pieces(result)

    # ------------------------------------------------------------------ #

    def _build_draft(self, block, ctx, headers, rows) -> ChunkDraft:
        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
        )
        # 章节路径由装配器统一拼进向量文本，此处只给 key-value 正文，避免重复前缀
        return ChunkDraft.of(
            self.render_markdown_table(headers, rows),
            self.render_key_value_rows(headers, rows),
            metadata,
        )

    # ---------------- KV 渲染（展示与预算度量共用） ---------------- #

    def render_key_value_rows(self, headers: List[str], rows: List[List[str]]) -> str:
        lines = []
        for row in rows:
            line = self.render_key_value_row(headers, row)
            if line:
                lines.append(line)
        return "\n".join(lines)

    def render_key_value_row(self, headers: List[str], row: List[str]) -> str:
        """单行渲染成「列名: 值」，"; " 拼接、跳过空值、整行空返回空串；同时用作预算切分的行体量度量"""
        parts = []
        for col, value in enumerate(row):
            if value is None or value == "":
                continue
            key = headers[col] if col < len(headers) else ""
            if key:
                parts.append(f"{self.one_line(key)}: {self.one_line(value)}")
            else:
                parts.append(self.one_line(value))
        return "; ".join(parts)

    @staticmethod
    def one_line(text: str) -> str:
        """把 cell 内换行压成空格：key 与 value 之间夹一个断行会影响检索"""
        return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

    # ---------------- markdown 展示渲染 ---------------- #

    def render_markdown_table(self, headers: List[str], rows: List[List[str]]) -> str:
        lines = []
        if headers or rows:
            lines.append(self._append_row(headers))
            lines.append(self._append_separator(len(headers)))
        for row in rows:
            lines.append(self._append_row(row))
        return "\n".join(lines)

    def _append_row(self, cells: List[str]) -> str:
        return "|" + "".join(f" {self.sanitize_cell(c)} |" for c in cells)

    @staticmethod
    def sanitize_cell(cell: Optional[str]) -> str:
        """清洗 cell 以适配 markdown 表格语法

        单元格内换行（Excel Alt+Enter）转 <br>，裸换行会从中间截断表格行、使整块退化成普通段落；
        竖线转义，cell 内的字面 | 会被误判为列分隔。
        """
        if cell is None or cell == "":
            return ""
        return cell.replace("|", "\\|").replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")

    @staticmethod
    def _append_separator(col_count: int) -> str:
        return "|" + "---|" * max(0, col_count)
