# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.heading_chunker - 标题分块器（对应 Java HeadingChunker + HeadingHandler）

HeadingHandler：
    按原始 heading 级别弹栈，维护调度器持有的章节路径。无状态，摄取并发共用同一实例，
    路径由调用方持有并逐块传入。级别必须一起留着：只看路径深度无法判断新标题该挂在哪一级下，
    不以 H1 开头的文档会把同级章节层层嵌套。

HeadingChunker：
    标题按原文位置回到正文。标题不产块的话，content 就不是文档原貌而是被剥掉全部结构的裸正文，
    命中的块回填模型时也无从判断出自哪一节；井号数取原始级别，不按路径深度重算。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.HeadingHandler
    - com.nageoffer.ai.ragent.core.chunk.blockaware.HeadingChunker
"""
from __future__ import annotations

from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import HeadingBlock
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft


class HeadingHandler:
    """标题处理器：按原始 heading 级别弹栈，维护章节路径（对应 Java HeadingHandler，无状态）"""

    class Outline:
        """章节路径连同各级的原始 heading 级别（对应 Java Outline record，不可变）

        级别必须一起留着：只看路径深度无法判断新标题该挂在哪一级下，不以 H1 开头的文档会把同级章节层层嵌套。
        """

        EMPTY: "HeadingHandler.Outline" = None  # 类属性（无注解，非 dataclass field）

        def __init__(self, path=(), levels=()):
            self._path = tuple(path or ())
            self._levels = tuple(levels or ())

        def path(self) -> tuple:
            return self._path

        def levels(self) -> tuple:
            return self._levels

        def __eq__(self, other):
            return (
                isinstance(other, HeadingHandler.Outline)
                and self._path == other._path
                and self._levels == other._levels
            )

        def __repr__(self):
            return f"Outline(path={self._path!r}, levels={self._levels!r})"

    # EMPTY 在类体后赋值（引用自身类型）
    Outline.EMPTY = Outline()

    def update(self, current: Optional["HeadingHandler.Outline"], heading: Optional[HeadingBlock]) -> "HeadingHandler.Outline":
        """根据 heading 更新章节路径，入参与返回值都不可变（对应 Java update）"""
        base = current if current is not None else HeadingHandler.Outline.EMPTY
        if heading is None:
            return base
        level = max(1, heading.level)

        # 弹掉同级与更深的祖先，真正的父级是最近一个级别更小的标题
        keep = len(base.levels())
        while keep > 0 and base.levels()[keep - 1] >= level:
            keep -= 1

        path = list(base.path()[:keep])
        levels = list(base.levels()[:keep])
        path.append(heading.text if heading.text is not None else "")
        levels.append(level)
        return HeadingHandler.Outline(path, levels)


class HeadingChunker(BlockChunker[HeadingBlock]):
    """标题 chunker：标题按原文位置回到正文（对应 Java HeadingChunker）"""

    MAX_LEVEL = 6

    def block_type(self) -> type:
        return HeadingBlock

    def chunk(self, block: Optional[HeadingBlock], ctx: ChunkContext) -> List[ChunkDraft]:
        if block is None or not (block.text or "").strip():
            return []
        text = block.text.strip()
        metadata = ChunkMetadata(
            outline_path=list(ctx.outline_path),
            source_file=block.provenance.source_file if block.provenance else None,
            sheet_name=block.provenance.sheet_name if block.provenance else None,
        )
        level = min(self.MAX_LEVEL, max(1, block.level))
        # 向量文本不带井号，markdown 标记对嵌入模型是零信息 token
        return [ChunkDraft.of_heading("#" * level + " " + text, text, metadata)]
