# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.code_chunker - 代码分块器（对应 Java CodeChunker）

优先整块保留，超出容忍上限才按行边界降级切分，每块重复围栏。
半截行与缺失围栏会破坏渲染与理解，所以默认不切；但 txt 的缩进段落会被解析成代码块、
真代码文件本身也可能远超预算，单块顶穿嵌入模型输入上限会被静默截断、尾部等于没入库。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.CodeChunker
"""
from __future__ import annotations

from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import CodeBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft


class CodeChunker(BlockChunker[CodeBlock]):
    """代码块 chunker：优先整块保留，超出容忍上限才按行边界降级切分（对应 Java CodeChunker）"""

    def block_type(self) -> type:
        return CodeBlock

    def chunk(self, block: Optional[CodeBlock], ctx: ChunkContext) -> List[ChunkDraft]:
        if block is None:
            return []
        language = block.language if block.language is not None else ""
        code = block.code if block.code is not None else ""

        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
        )

        if len(code) <= ctx.budget.tolerance_chars():
            segments = [code]
        else:
            segments = self.split_by_lines(code, ctx.budget.max_chars)

        result = []
        for segment in segments:
            markdown = f"```{language}\n{segment}\n```"
            result.append(ChunkDraft.of(markdown, segment, metadata))
        return ChunkDraft.pieces(result)

    @staticmethod
    def split_by_lines(code: str, max_chars: int) -> List[str]:
        """按行累加切分：单行超预算时整行独立成块，绝不从行中间切断"""
        segments: List[str] = []
        current = ""
        for line in code.split("\n", -1):
            addition = len(line) if not current else len(current) + 1 + len(line)
            if current and addition > max_chars:
                segments.append(current)
                current = ""
            current = current + "\n" + line if current else line
        if current:
            segments.append(current)
        return segments if segments else [code]
