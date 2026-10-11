# -*- coding: utf-8 -*-
"""
rag.retrieval.parent_resolver - Child 召回 → Parent 回填（对应方案 3.4.4）

召回粒度与生成粒度分离的兑现处：向量库只存 Child，召回命中的也是 Child；
但生成要的是完整上下文，故在后处理链末端把 Child 换成（或补上）它所属的 Parent 正文。

为什么放在后处理链之后而不是链中：
    去重 / RRF 融合 / Rerank / 证据闸门全是按「召回单元」设计的——它们比较的是向量相似度与
    精排分，Child 才是这些分数的载体。先让整条链按 Child 跑完，再回填父块，等于不改动
    任何既有环节的语义；反过来先回填再去重，同一父块会被多个 Child 带进来算成多条。

父块内容从关系库取（t_knowledge_chunk 的 parent_id 行）：父块不进向量库，除了关系库，
没有第二处能拿到它的正文。

对应 ragent 源码：
    - 本仓库新增（Java 侧无对应类，方案第四阶段设计）
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set

from core.llm.schema import RetrievedChunk
from storage.database import Condition, DatabaseClient

logger = logging.getLogger(__name__)


class ParentChunkResolver:
    """Child 召回结果的父块回填器（对应方案 3.4.4）

    Args:
        db:   关系库访问客户端（父块正文与 parent_id 关系都在 t_knowledge_chunk）
        mode: REPLACE（用父块替换子块，默认）| APPEND（子块后追加父块）
    """

    MODE_REPLACE = "REPLACE"
    MODE_APPEND = "APPEND"
    MODES = (MODE_REPLACE, MODE_APPEND)

    CHUNK_TABLE = "t_knowledge_chunk"

    def __init__(self, db: DatabaseClient, mode: str = MODE_REPLACE):
        normalized = (mode or "").strip().upper()
        if normalized not in self.MODES:
            # 配置拼错时静默退化成「不回填」会让整条链路看起来生效实则没生效，故启动即失败
            raise ValueError(f"parent-child 回填模式必须是 {self.MODES} 之一，实际 {mode!r}")
        self._db = db
        self._mode = normalized

    async def resolve(
        self, chunks: List[RetrievedChunk], committed_attempt_ids=None,
    ) -> List[RetrievedChunk]:
        """按 parent_id 回填父块；无父子关系的块原样保留。

        committed_attempt_ids（P9 §8 版本快照）：非 None 时父块回表只接受白名单内
        attempt 的行（attempt 域写含 build_attempt_id 列；未迁移行被排除）。
        """
        if not chunks:
            return []

        attempt_condition = (
            [Condition.in_("build_attempt_id", list(committed_attempt_ids))]
            if committed_attempt_ids is not None else []
        )
        parent_id_by_chunk = self._load_parent_ids([c.id for c in chunks], attempt_condition)
        # 召回结果自带的 parent_id（元数据富化写入）优先，查不到再回落到关系库那一份
        parent_ids: Set[str] = set()
        for chunk in chunks:
            parent_id = self._parent_id_of(chunk, parent_id_by_chunk)
            if parent_id:
                parent_ids.add(parent_id)
        if not parent_ids:
            return chunks

        parent_rows = self._load_parents(parent_ids, attempt_condition)
        if not parent_rows:
            return chunks

        best_score = self._best_scores(chunks, parent_id_by_chunk, parent_rows)
        if self._mode == self.MODE_APPEND:
            return self._append(chunks, parent_id_by_chunk, parent_rows, best_score)
        return self._replace(chunks, parent_id_by_chunk, parent_rows, best_score)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _parent_id_of(chunk: RetrievedChunk, parent_id_by_chunk: Dict[str, str]) -> Optional[str]:
        """本条命中所属的父块 ID：自带优先，其次关系库回表结果"""
        if chunk.parent_id is not None and str(chunk.parent_id).strip():
            return str(chunk.parent_id)
        return parent_id_by_chunk.get(chunk.id)

    def _replace(
        self,
        chunks: List[RetrievedChunk],
        parent_id_by_chunk: Dict[str, str],
        parent_rows: Dict[str, dict],
        best_score: Dict[str, Optional[float]],
    ) -> List[RetrievedChunk]:
        """用父块替换子块：同一父块被多个子块命中时只出一份，位置取首次命中处

        父块缺失（行已被删 / 单块人工编辑过）时保留原子块：宁可给碎一点的证据，
        也不让一次父子关系失效吞掉整条召回。
        """
        result: List[RetrievedChunk] = []
        emitted: Set[str] = set()
        for chunk in chunks:
            parent_id = self._parent_id_of(chunk, parent_id_by_chunk)
            row = parent_rows.get(parent_id) if parent_id else None
            if row is None:
                result.append(chunk)
                continue
            if parent_id in emitted:
                continue
            emitted.add(parent_id)
            result.append(self._to_parent(row, chunk, best_score.get(parent_id)))
        return result

    def _append(
        self,
        chunks: List[RetrievedChunk],
        parent_id_by_chunk: Dict[str, str],
        parent_rows: Dict[str, dict],
        best_score: Dict[str, Optional[float]],
    ) -> List[RetrievedChunk]:
        """子块后追加父块（同一父块只追加一次）：保留精确命中，同时给出完整上下文"""
        result: List[RetrievedChunk] = []
        emitted: Set[str] = set()
        for chunk in chunks:
            result.append(chunk)
            parent_id = self._parent_id_of(chunk, parent_id_by_chunk)
            row = parent_rows.get(parent_id) if parent_id else None
            if row is None or parent_id in emitted:
                continue
            emitted.add(parent_id)
            result.append(self._to_parent(row, chunk, best_score.get(parent_id)))
        return result

    def _best_scores(
        self,
        chunks: List[RetrievedChunk],
        parent_id_by_chunk: Dict[str, str],
        parent_rows: Dict[str, dict],
    ) -> Dict[str, Optional[float]]:
        """每个父块的得分：取组内子块最高分，替换后排序与截断仍按「最相关那条」为准"""
        scores: Dict[str, Optional[float]] = {}
        for chunk in chunks:
            parent_id = self._parent_id_of(chunk, parent_id_by_chunk)
            if not parent_id or parent_id not in parent_rows:
                continue
            if parent_id not in scores or RetrievedChunk.by_score_desc(chunk) > self._score_of(
                scores[parent_id]
            ):
                scores[parent_id] = chunk.score
        return scores

    @staticmethod
    def _score_of(score: Optional[float]) -> float:
        """裸分数 → 可比较的排序值：复用 by_score_desc 的 None / 非有限值沉底规则，两处口径不许走偏"""
        return RetrievedChunk.by_score_desc(RetrievedChunk(id="", text="", score=score))

    @staticmethod
    def _to_parent(row: dict, child: RetrievedChunk, score: Optional[float]) -> RetrievedChunk:
        """父块命中结果：展示文本换父块正文，作用域与文档归属沿用命中它的那个子块

        collection_name 必须继承：检索作用域的意图归因是按 collection 反查的，
        父块本身不在任何 collection 里，丢了这层继承会让回填后的证据变成「无归属」而丢失意图绑定。

        chunk_index 也继承子块那一份，而不是取父块行的合成序号：父块行的序号被安排在全部 Child 之后
        （父块不进向量库，序号只服务于落库定位），而 PromptFormatter 会按 chunk_index 升序还原文档顺序——
        一篇既成组（换成父块）又有未成组 standalone 块的文档，两侧序号不在同一坐标系，
        直接取父块序号会把 standalone 块全部排到成组节之前，原文顺序被打乱。
        子块序号落在它所属节内，用它定位既保文档顺序、也与本条「取首次命中处」的落位口径一致。
        """
        doc_id = row.get("doc_id") if row.get("doc_id") is not None else child.doc_id
        chunk_index = child.chunk_index if child.chunk_index is not None else row.get("chunk_index")
        return RetrievedChunk(
            id=row.get("id"),
            text=row.get("content") or "",
            score=score,
            rerank_score=child.rerank_score,
            collection_name=child.collection_name,
            doc_id=doc_id,
            chunk_index=chunk_index,
            doc_name=child.doc_name,
            parent_id=None,
        )

    # ------------------------------------------------------------------ #

    def _load_parent_ids(
        self, chunk_ids: List[str], extra_conditions=None
    ) -> Dict[str, str]:
        """按命中块 ID 批量回表取 parent_id（只取已删标记为 0 的行；版本快照追加 attempt 条件）"""
        ids = _non_blank_deduped(chunk_ids)
        if not ids:
            return {}
        rows = self._db.select_rows(
            self.CHUNK_TABLE,
            columns=["id", "parent_id"],
            where=[Condition.in_("id", ids), Condition.eq("deleted", 0), *(extra_conditions or [])],
        )
        result: Dict[str, str] = {}
        for row in rows or []:
            chunk_id = row.get("id")
            parent_id = row.get("parent_id")
            if chunk_id is None or parent_id is None:
                continue
            if isinstance(parent_id, str) and not parent_id.strip():
                continue
            result[chunk_id] = parent_id
        return result

    def _load_parents(self, parent_ids: Set[str], extra_conditions=None) -> Dict[str, dict]:
        """按父块 ID 批量回表取正文与归属信息（版本快照追加 attempt 条件，§8 白名单消费方）"""
        ids = _non_blank_deduped(list(parent_ids))
        if not ids:
            return {}
        rows = self._db.select_rows(
            self.CHUNK_TABLE,
            columns=["id", "doc_id", "chunk_index", "content"],
            where=[Condition.in_("id", ids), Condition.eq("deleted", 0), *(extra_conditions or [])],
        )
        result: Dict[str, dict] = {}
        for row in rows or []:
            parent_id = row.get("id")
            if parent_id is None:
                continue
            result[parent_id] = row
        return result


def _non_blank_deduped(values: List[Optional[str]]) -> List[str]:
    """过滤空 / 空白字符串 + 去重保序（与 ChunkMetadataResolver 同一口径）"""
    seen = set()
    result: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result