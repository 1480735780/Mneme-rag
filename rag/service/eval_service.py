# -*- coding: utf-8 -*-
"""
rag.service.eval_service - 评测检索服务（对应 Java EvalController 链路）

评测端点 GET /rag/eval 的纯检索证据聚合（无 LLM 输出，对齐 Java EvalController）：
    rewrite_with_split → intent_resolver.resolve → 按子问题多通道检索 → 摊平 intent_chunks 去重 →
    两跳 docId 解析（chunk_id → t_knowledge_chunk.doc_id → t_knowledge_document.doc_name 剥后缀）。

对齐 Java EvalController / EvalResponse 的字段语义：
    - retrievedDocIds：doc 维度去重、剥文件后缀（业务码，对齐评测集 reference_doc_ids）
    - retrievedChunkIds / retrievedContexts：chunk 维度，去重保序
    - retrievedContextDocIds：与 retrievedContexts 一一对应、保留 null、不去重（chunk 级指标按 index 取用）
    - mcpContext / hasMcp / hasKb：本版本聚焦 KB 检索证据（MCP 分支标注为后续可选项，hasMcp 恒 False）
    - subIntents / intentLeafIds：子问题列表 + 每子问题 top-1 意图叶子 id（无候选为 null）
    - latencyMs：总耗时

stripExtension 逐字对齐 Java（lastIndexOf('.')，dot>0 且 <len-1 才剥，否则原样）：
    a.tar.gz → a.tar / a. → a / 无点 → 原样（B5）

前置条件（D9）：评测环境须 LLM 就绪 + 检索通道启用（本服务从装配好的引擎提取组件，
引擎未就绪时 eval_service 为 None、端点不挂载）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from core.llm.schema import RetrievedChunk
from rag.retrieval.schema import MULTI_CHANNEL_KEY

logger = logging.getLogger(__name__)


class EvalRetrievalService:
    """评测检索聚合服务（组件由 wiring 从引擎提取注入；无状态）"""

    def __init__(
        self,
        query_rewrite_service,
        intent_resolver,
        retrieval_engine,
        budget,
        scope_resolver,
        chunk_dao,
        db,
    ):
        self._rewrite = query_rewrite_service
        self._intent_resolver = intent_resolver
        self._retrieval = retrieval_engine
        self._budget = budget
        self._scope_resolver = scope_resolver
        self._chunk_dao = chunk_dao
        self._db = db

    async def load_eval(self, question: str) -> Dict[str, Any]:
        """执行评测检索链路 → snake_case dict（controller 边界 camelize）"""
        start = time.time()
        try:
            rewrite_result = await self._rewrite.rewrite_with_split(question)
        except Exception:  # noqa: BLE001 —— 单问题评测失败不致命
            logger.warning("评测改写失败，question=%s", question, exc_info=True)
            return self._empty_result(question, int((time.time() - start) * 1000))

        sub_intents = await self._intent_resolver.resolve(rewrite_result)

        merged: Dict[str, List[RetrievedChunk]] = {}
        for si in sub_intents:
            try:
                result = await self._retrieval.retrieve_knowledge_channels(
                    si, self._budget, self._scope_resolver
                )
            except Exception:  # noqa: BLE001 —— 单子问题检索失败降级为空
                logger.warning("评测子问题检索失败，question=%s", si.sub_question, exc_info=True)
                continue
            if not result.chunks:
                continue
            for intent_id, chunks in result.group_by_intent(MULTI_CHANNEL_KEY).items():
                if chunks:
                    merged.setdefault(intent_id, []).extend(chunks)

        unique_chunks = self._flatten_dedup(merged)
        chunk_ids = [c.id for c in unique_chunks if c.id]
        contexts = [c.text for c in unique_chunks]
        context_doc_ids = self._resolve_context_doc_ids(unique_chunks)
        doc_ids = self._dedup_non_blank(context_doc_ids)
        has_kb = bool(unique_chunks)

        sub_intents_list = [si.sub_question for si in sub_intents]
        intent_leaf_ids = self._extract_top_leaf_ids(sub_intents)

        return {
            "retrieved_doc_ids": doc_ids,
            "retrieved_chunk_ids": chunk_ids,
            "retrieved_contexts": contexts,
            "retrieved_context_doc_ids": context_doc_ids,
            "mcp_context": None,
            "has_mcp": False,
            "has_kb": has_kb,
            "sub_intents": sub_intents_list,
            "intent_leaf_ids": intent_leaf_ids,
            "latency_ms": int((time.time() - start) * 1000),
        }

    def _empty_result(self, question: str, latency_ms: int) -> Dict[str, Any]:
        return {
            "retrieved_doc_ids": [],
            "retrieved_chunk_ids": [],
            "retrieved_contexts": [],
            "retrieved_context_doc_ids": [],
            "mcp_context": None,
            "has_mcp": False,
            "has_kb": False,
            "sub_intents": [question],
            "intent_leaf_ids": [None],
            "latency_ms": latency_ms,
        }

    # ==================== 聚合辅助（对齐 Java EvalController 私有方法） ====================

    @staticmethod
    def _flatten_dedup(merged: Dict[str, List[RetrievedChunk]]) -> List[RetrievedChunk]:
        """摊平 intentChunks 并按 chunk id 首现去重（对齐 Java flattenChunks）"""
        seen = set()
        unique: List[RetrievedChunk] = []
        for chunks in merged.values():
            for chunk in chunks:
                if chunk is None or not chunk.id:
                    continue
                if chunk.id in seen:
                    continue
                seen.add(chunk.id)
                unique.append(chunk)
        return unique

    def _resolve_context_doc_ids(self, chunks: List[RetrievedChunk]) -> List[Optional[str]]:
        """两跳：chunkId → t_knowledge_chunk.doc_id → t_knowledge_document.doc_name 剥后缀

        与 chunks 一一对应、保留 null、不去重（对齐 Java resolveContextDocIds）。
        """
        if not chunks:
            return []
        chunk_ids = [c.id for c in chunks if c.id]
        if not chunk_ids:
            return [None] * len(chunks)
        # 第一跳：chunkId → docId
        chunk_id_to_doc_id: Dict[str, str] = {}
        for row in self._chunk_dao.select_by_ids(chunk_ids):
            if row.get("id") and row.get("doc_id"):
                chunk_id_to_doc_id[row["id"]] = row["doc_id"]
        # 第二跳：docId → doc_name（剥后缀）
        doc_ids = sorted({d for d in chunk_id_to_doc_id.values() if d})
        internal_to_biz: Dict[str, str] = {}
        if doc_ids:
            from storage.database import Condition

            for row in self._db.select_rows(
                "t_knowledge_document",
                columns=["id", "doc_name"],
                where=[Condition.in_("id", doc_ids)],
            ):
                if row.get("id") and row.get("doc_name"):
                    internal_to_biz[row["id"]] = self.strip_extension(row["doc_name"])
        return [
            internal_to_biz.get(chunk_id_to_doc_id[c.id]) if c.id else None
            for c in chunks
        ]

    @staticmethod
    def strip_extension(doc_name: Optional[str]) -> Optional[str]:
        """剥文件后缀（逐字对齐 Java：lastIndexOf('.')，dot>0 且 <len-1 才剥，否则原样）"""
        if doc_name is None:
            return None
        dot = doc_name.rfind(".")
        if 0 < dot < len(doc_name) - 1:
            return doc_name[:dot]
        return doc_name

    @staticmethod
    def _dedup_non_blank(values: List[Optional[str]]) -> List[str]:
        """首现去重并过滤空（对齐 Java dedupNonBlank）"""
        seen = set()
        result = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    @staticmethod
    def _extract_top_leaf_ids(sub_intents) -> List[Optional[str]]:
        """每子问题 top-1 意图叶子 id（无候选为 null；对齐 Java extractTopLeafIds）"""
        result = []
        for si in sub_intents:
            scores = getattr(si, "node_scores", None) or []
            result.append(scores[0].node.id if scores and scores[0].node else None)
        return result
