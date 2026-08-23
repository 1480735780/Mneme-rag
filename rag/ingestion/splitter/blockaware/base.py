# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.base - BlockChunker 抽象（对应 Java BlockChunker）

Block 类型专属的切分器：每个实现自报处理哪个 Block 类型、怎么切。
前者让调度器靠查表工作，新增 Block 类型只补一个实现即可；能否与邻居并块不由 Block 类型决定，
由 ChunkPacker 按预算算出来。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.BlockChunker
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, List, Type, TypeVar

from rag.ingestion.parser.model import Block
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.model import ChunkDraft

B = TypeVar("B", bound=Block)


class BlockChunker(ABC, Generic[B]):
    """Block 类型专属切分器抽象（对应 Java BlockChunker<B>）

    子类自报 block_type() 并由调度器注册；chunk() 把单个 Block 切分为若干草稿，可能为空。
    切与不切的判据全类型统一：整块撑得住 tolerance_chars() 就不切，超出才按块大小降级切分，
    且切点一律落在结构边界（行、表格行、列表项、句末）上。
    """

    @abstractmethod
    def block_type(self) -> Type[B]:
        """注册键：本 chunker 处理的 Block 类型"""
        ...

    @abstractmethod
    def chunk(self, block: B, ctx: ChunkContext) -> List[ChunkDraft]:
        """把单个 Block 切分为若干草稿，可能为空；序号与块 ID 由装配阶段统一分配"""
        ...
