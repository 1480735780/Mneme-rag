# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.dispatcher - 分块调度器（对应 Java BlockAwareChunkerDispatcher）

Block 类型 → chunker 查表分发，同一类型被两个 chunker 认领时启动即失败。
标题先更新章节路径再照常分发，于是它拿到的是含自己在内的路径，与其后正文同节而自然同块；
流程固定为分发产草稿 → 按节打包 → 统一装配，装配留在末端是因为向量文本要拼章节前缀，
而打包只能发生在拼前缀之前。

heading_handler / packer 以鸭子类型注入（接口在 2.4 / 2.11 落地），此处定义最小契约：
    - heading_handler.update(outline, heading) -> outline，outline.path() -> 章节路径序列
    - packer.pack(drafts, budget) -> List[ChunkDraft]

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.BlockAwareChunkerDispatcher
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from common.exception.business import ServiceException
from rag.ingestion.parser.model import Block, HeadingBlock
from rag.ingestion.splitter.base import ChunkerDispatcher
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkAssembler, ChunkDraft
from rag.ingestion.splitter.base import ChunkBudget


class BlockAwareChunkerDispatcher(ChunkerDispatcher):
    """分块调度器：Block 类型 → chunker 查表分发（对应 Java BlockAwareChunkerDispatcher）

    实现 ChunkerDispatcher 契约，可直接注入 ChunkingService 切换 BlockAware 分块路径。
    """

    def __init__(
        self,
        packer: Any,
        heading_handler: Optional[Any] = None,
        chunkers: Optional[List[BlockChunker]] = None,
    ):
        """
        Args:
            packer:         块打包器（ChunkPacker 等价物），需提供 pack(drafts, budget)
            heading_handler: 标题处理器（HeadingHandler 等价物，2.4 落地），None 时标题不累积路径
            chunkers:       各 Block 类型专属切分器列表；同一类型被两个认领即启动失败
        """
        self._packer = packer
        self._heading_handler = heading_handler
        self._registry: Dict[Type[Block], BlockChunker] = {}
        for chunker in chunkers or []:
            block_type = chunker.block_type()
            previous = self._registry.get(block_type)
            if previous is not None:
                raise ServiceException(
                    f"Block 分块器注册冲突：类型={block_type.__name__} 同时被 "
                    f"{type(previous).__name__} 与 {type(chunker).__name__} 认领"
                )
            self._registry[block_type] = chunker

    def dispatch(self, blocks: List[Block], budget: ChunkBudget):
        """把 Block 列表切分为有序块，序号从 0 单调递增（返回 List[ChunkData]）"""
        if not blocks:
            return []

        outline = (
            getattr(self._heading_handler, "EMPTY", None)
            if self._heading_handler is not None
            else None
        )
        drafts: List[ChunkDraft] = []
        for block in blocks:
            if self._heading_handler is not None and isinstance(block, HeadingBlock):
                outline = self._heading_handler.update(outline, block)
            path = outline.path() if outline is not None else ()
            drafts.extend(self._chunk_one(block, ChunkContext.of(list(path), budget)))

        return ChunkAssembler.assemble_all(self._packer.pack(drafts, budget))

    def _chunk_one(self, block: Block, ctx: ChunkContext) -> List[ChunkDraft]:
        """按块类型查表分发到具体 chunker，未认领即失败"""
        chunker = self._registry.get(type(block))
        if chunker is None:
            raise ServiceException(f"没有 chunker 认领 Block 类型：{type(block).__name__}")
        return chunker.chunk(block, ctx)


def build_block_aware_dispatcher(
    chunkers: Optional[List[BlockChunker]] = None,
    heading_handler: Optional[Any] = None,
    packer: Optional[Any] = None,
) -> BlockAwareChunkerDispatcher:
    """默认装配 BlockAware 分块器：全部 7 个 chunker + HeadingHandler + ChunkPacker

    chunkers / heading_handler / packer 可覆盖注入（测试或定制用）。
    """
    if chunkers is None:
        from rag.ingestion.splitter.blockaware.code_chunker import CodeChunker
        from rag.ingestion.splitter.blockaware.heading_chunker import HeadingChunker, HeadingHandler
        from rag.ingestion.splitter.blockaware.html_table_chunker import HtmlTableChunker
        from rag.ingestion.splitter.blockaware.image_chunker import ImageChunker
        from rag.ingestion.splitter.blockaware.list_chunker import ListChunker
        from rag.ingestion.splitter.blockaware.paragraph_chunker import ParagraphChunker
        from rag.ingestion.splitter.blockaware.table_chunker import TableChunker

        chunkers = [
            HeadingChunker(),
            ParagraphChunker(),
            TableChunker(),
            ListChunker(),
            CodeChunker(),
            ImageChunker(),
            HtmlTableChunker(),
        ]
        if heading_handler is None:
            heading_handler = HeadingHandler()
    if packer is None:
        from rag.ingestion.splitter.blockaware.packer import ChunkPacker

        packer = ChunkPacker()
    return BlockAwareChunkerDispatcher(
        packer=packer,
        heading_handler=heading_handler,
        chunkers=chunkers,
    )
