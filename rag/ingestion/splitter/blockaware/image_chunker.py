# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.image_chunker - 图片分块器（对应 Java ImageChunker）

一图一块，展示文本是描述 + markdown 图片链接，向量文本只取描述。
图片 URL 进向量是纯噪声，只在无描述时（如 MinerU 抽图）才回落到链接本身；声明为可流动，
让图与它的前导语 / 解释文字同块，检索命中即带图。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.ImageChunker
"""
from __future__ import annotations

from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import ImageBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft


class ImageChunker(BlockChunker[ImageBlock]):
    """图片 chunker：一图一块，展示含链接、向量只取描述（对应 Java ImageChunker）"""

    def block_type(self) -> type:
        return ImageBlock

    def chunk(self, block: Optional[ImageBlock], ctx: ChunkContext) -> List[ChunkDraft]:
        if block is None or block.asset is None:
            return []
        asset = block.asset
        markdown = f"![{self._pick_caption(block)}]({asset.public_url})"

        description = block.description
        has_description = description is not None and bool(description.strip())
        if has_description:
            content = description.strip() + "\n\n" + markdown
            body = description.strip()
        else:
            content = markdown
            body = None

        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
            assets=[asset],
        )
        return [ChunkDraft.of(content, body, metadata)]

    @staticmethod
    def _pick_caption(block: ImageBlock) -> str:
        if block.caption:
            return block.caption
        if block.alt_text:
            return block.alt_text
        return ""
