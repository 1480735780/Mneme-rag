# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.parent_child_builder - 结构组父子块构建器（对应方案 3.4.1）

父子块的动机：召回要「小」——块越小，向量越聚焦，越容易命中；生成要「大」——上下文越完整，
答案越不容易断章取义。同一份切分结果服务不了两个相反的目标，于是让 Child 进向量库（召回单元）、
Parent 只进关系库（生成时按 parent_id 回填）。这里负责在分块层把两者的关系建出来。

为什么挂在「打包之后、装配之前」：
    - 打包之后：Child 就是最终召回粒度，与向量库里的一行严格一一对应；早于打包标记，
      标记会随 packer 的 merge/flush 被合并或改写，标出来的 parent_id 与真实入库块对不上。
    - 装配之前：ChunkDraft 还是可自由打标的形态，ChunkData 一旦构造就定了内容与 embedding_text。

为什么用「章节路径可前缀衔接」判定同一节：
    ChunkPacker 已把节边界用于跨节合并与节内切分，打包产物上不再保留节标记；但打包不会改动
    metadata.outline_path，而同一节的块路径必然一条是另一条的前缀（父标题节 [五] 与它的子标题节
    [五, 5.1] 会归入同一节；兄弟子节 [五, 5.1] 与 [五, 5.2] 则互不为前缀、分属两节），
    故路径前缀关系是节身份在打包产物上唯一还留着的等价判据。

对应 ragent 源码：
    - 本仓库新增（Java 侧无对应类，方案第四阶段设计）
"""
from __future__ import annotations

from dataclasses import replace
from typing import List, Tuple

from core.llm.schema import (
    CHUNK_ROLE_CHILD,
    CHUNK_ROLE_PARENT,
    CHUNK_ROLE_STANDALONE,
    ChunkData,
)
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.model import ChunkAssembler, ChunkDraft
from rag.ingestion.splitter.blockaware.packer import ChunkPacker


class ParentChildBuilder:
    """把成块的 Child 草案按结构组归并，为每组产出 Parent 块并回标 Child 的 parent_id"""

    def build(
        self,
        drafts: List[ChunkDraft],
        budget: ChunkBudget,
    ) -> Tuple[List[ChunkDraft], List[ChunkData]]:
        """标记 Child 并产出 Parent（对应方案 3.4.1 的 build）

        Args:
            drafts: ChunkPacker 打包后的草案，顺序即文档顺序（装配阶段按位置分配序号）
            budget: 分块预算，父块体量上限取 tolerance_chars()（与节内切分同一口径）

        Returns:
            (tagged_drafts, parent_chunks)：
            - tagged_drafts 与入参等长同序，成组的块标 parent_id + role=child，未成组标 role=standalone
            - parent_chunks 每组一个，序号接在全部 Child 之后（Parent 不进向量库，序号只服务于落库定位）
        """
        if not drafts:
            return [], []

        tagged: List[ChunkDraft] = []
        parents: List[ChunkData] = []
        for group in self._group(drafts, budget):
            if len(group) < 2:
                # 单块成组没有父子可言：Parent 等于它自己，白多一行且召回/生成同粒度
                tagged.extend(
                    replace(draft, parent_id=None, chunk_role=CHUNK_ROLE_STANDALONE)
                    for draft in group
                )
                continue
            parent_id = ChunkAssembler.next_chunk_id()
            tagged.extend(
                replace(draft, parent_id=parent_id, chunk_role=CHUNK_ROLE_CHILD)
                for draft in group
            )
            parents.append(self._assemble_parent(group, parent_id, len(drafts) + len(parents)))
        return tagged, parents

    # ------------------------------------------------------------------ #

    @staticmethod
    def _group(drafts: List[ChunkDraft], budget: ChunkBudget) -> List[List[ChunkDraft]]:
        """连续草案按「同节 + 不超父块体量上限」切组

        上限取 tolerance_chars 而非 max_chars：父块是生成上下文，多带几倍正文是收益；
        但无上限的话，一篇只在章一级设标题的长文档会攒出一个上万字的父块，
        回填后直接顶爆生成上下文预算，故仍需一条硬上限。
        """
        max_chars = budget.tolerance_chars()
        groups: List[List[ChunkDraft]] = []
        current: List[ChunkDraft] = []
        current_len = 0
        for draft in drafts:
            if current:
                grown = current_len + len(ChunkPacker.SEPARATOR) + ChunkPacker.content_length(draft)
                if not ParentChildBuilder._same_group(current[-1], draft) or grown > max_chars:
                    groups.append(current)
                    current = []
                    current_len = 0
            if current:
                current_len += len(ChunkPacker.SEPARATOR)
            current.append(draft)
            current_len += ChunkPacker.content_length(draft)
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _same_group(left: ChunkDraft, right: ChunkDraft) -> bool:
        """两个相邻块是否属于同一节：章节路径一条是另一条的前缀（含相等）即为同节"""
        left_path = left.metadata.outline_path or []
        right_path = right.metadata.outline_path or []
        return ParentChildBuilder._is_prefix(left_path, right_path) or ParentChildBuilder._is_prefix(
            right_path, left_path
        )

    @staticmethod
    def _is_prefix(short: List[str], long_path: List[str]) -> bool:
        """short 是否为 long_path 的完整前缀（兄弟节 [五,5.1] 与 [五,5.2] 互不为前缀，据此分节）"""
        if len(short) > len(long_path):
            return False
        return list(long_path[: len(short)]) == list(short)

    @staticmethod
    def _assemble_parent(group: List[ChunkDraft], parent_id: str, index: int) -> ChunkData:
        """Parent = 整节正文合并（复用 ChunkPacker.merge，与块合并同一套展示/检索文本规则）"""
        merged = replace(
            ChunkPacker.merge(group), parent_id=None, chunk_role=CHUNK_ROLE_PARENT
        )
        return ChunkAssembler.assemble(parent_id, index, merged)