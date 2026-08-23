# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.context - 切分上下文（对应 Java ChunkContext）

调度器遍历 Block 列表时构造并传给每个 chunker 的不可变上下文：
    - outline_path：章节路径，由 HeadingHandler 在遍历时累积（见 heading_chunker）
    - budget：分块预算，chunker 据此决策合并/切分/容忍

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.ChunkContext
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from rag.ingestion.splitter.base import ChunkBudget


@dataclass(frozen=True)
class ChunkContext:
    """切分上下文：调度器遍历 Block 列表时构造并传给每个 chunker（对应 Java ChunkContext record）

    Attributes:
        outline_path: 章节路径（根 → 叶），None 归一为空列表；frozen + tuple 保证不可变
        budget:       分块预算，chunker 据此决策
    """

    outline_path: List[str]
    budget: ChunkBudget

    def __post_init__(self):
        # Java record compact constructor：outlinePath null → List.of()，再 List.copyOf 防御拷贝；
        # frozen dataclass 下用 object.__setattr__ 改写为 tuple 实现真正的不可变
        object.__setattr__(
            self,
            "outline_path",
            tuple(self.outline_path) if self.outline_path is not None else (),
        )

    @staticmethod
    def of(outline_path: List[str], budget: ChunkBudget) -> "ChunkContext":
        """构造上下文（对应 Java of）"""
        return ChunkContext(outline_path, budget)
