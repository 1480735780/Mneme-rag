# -*- coding: utf-8 -*-
"""
rag.ingestion.index_payload - 双索引载荷（对应方案 3.4.2）

父子块落库时，「向量库只收 Child、关系库收 Child + Parent」这件事无法用单一
List[EmbeddedChunk] 表达：Parent 不进向量库（不参与召回），却必须进关系库
（生成时按 parent_id 回填正文）。故索引层入参从「一个块列表」升级为「两份块」，
由各 ChunkSink 各取所需。

Parent 不带向量：它不参与召回，嵌入一次纯属白烧 token 与存储；留 ChunkData 而不是
EmbeddedChunk，让类型上就写不出「给 Parent 配向量」这种无效组合。

对应 ragent 源码：
    - 本仓库新增（Java 侧无对应类，方案第四阶段设计）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from core.llm.schema import ChunkData, EmbeddedChunk


@dataclass
class IndexPayload:
    """
    索引载荷：Child（向量索引 + 关系库）与 Parent（仅关系库）

    Attributes:
        child_chunks:  已向量化的 Child / standalone 块，向量库与关系库都要写
        parent_chunks: 未向量化的 Parent 块，只写关系库（生成时回填用）
    """

    child_chunks: List[EmbeddedChunk] = field(default_factory=list)
    parent_chunks: List[ChunkData] = field(default_factory=list)

    def __post_init__(self):
        if self.child_chunks is None:
            self.child_chunks = []
        if self.parent_chunks is None:
            self.parent_chunks = []

    def has_parents(self) -> bool:
        """是否带父块：无父块时调用方可回落到既有单列表写入路径，行为与升级前完全一致"""
        return bool(self.parent_chunks)