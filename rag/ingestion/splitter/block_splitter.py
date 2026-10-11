# -*- coding: utf-8 -*-
"""
Block 感知切分器装配入口（对应 ragent blockaware/：标题/段落/表格/列表/代码）

实现位于同目录 blockaware/ 子包（7 类 chunker + HeadingHandler + ChunkPacker，
见 build_block_aware_dispatcher）；本模块只做装配门面，供 wiring 按
RAGENT_CHUNK_BLOCKAWARE_ENABLED（默认开）注入 ChunkingService，
关闭时回落 ChunkingService 默认的纯文本切分（TextChunkDispatcher）。
"""
from __future__ import annotations

from typing import Any, Optional

from rag.ingestion.splitter.base import ChunkerDispatcher
from rag.ingestion.splitter.blockaware.dispatcher import build_block_aware_dispatcher


def build_block_splitter(
    contextualizer: Optional[Any] = None,
    parent_child_builder: Optional[Any] = None,
) -> ChunkerDispatcher:
    """装配 Block 感知分发器：全部 7 个 chunker + HeadingHandler + ChunkPacker（默认配置）

    contextualizer 由 wiring 按 RAGENT_CHUNK_CONTEXTUAL_ENABLED 传入（M2 3.3.4）；None 即不增强。
    parent_child_builder 由 wiring 按 RAGENT_CHUNK_PARENT_CHILD_ENABLED 传入（3.4.6）；None 即不建父块。
    """
    return build_block_aware_dispatcher(
        contextualizer=contextualizer,
        parent_child_builder=parent_child_builder,
    )
