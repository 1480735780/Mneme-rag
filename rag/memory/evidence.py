# -*- coding: utf-8 -*-
"""
rag.memory.evidence - 会话级 Evidence 派生（Mneme-rag V2.1.1 / P-01）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 1 节 P-01

P-01 决策：ConversationState **不含 references slot**，装配阶段从 Evidence 派生。
本模块提供派生实现。

V2.1.1 阶段的临时数据源：
    t_message.sources JSONB 列（List[SourceRef] 序列化）
    - 含 index / docId / docName / sourceType / fileType / url / excerpt
    - 文档级去重后作为 evidence refs 的初始来源

Phase 7 完成 t_conversation_evidence 表后，本模块查询源切换到该表，
接口 `derive_evidence_refs(...)` 签名与语义保持稳定，
调用方（summary.py / state.py）无需改动。

排序策略（对齐 V2.1.1 P-01）：
    1. 按 doc_id 分组，每组保留"最近一次引用"（消息 id 最大）
    2. 组内多来源同一 doc 时取 index 最小者（index 是 SourceRef 的原始排序键）
    3. 排序：优先 score（当前用 1/index 近似），tie-breaker 用消息 id DESC
    4. Top-K 截断，默认 K=10
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from storage.database import Condition, DatabaseClient

from rag.memory.state import EvidenceRef

logger = logging.getLogger(__name__)

# 派生时扫描的最近 assistant 消息条数上限（足够覆盖典型会话证据密度，避免全表扫）
_SCAN_RECENT_ASSISTANT_MESSAGES = 50

# 默认返回 Top-K 证据条数
_DEFAULT_TOP_K = 10


def derive_evidence_refs(
    db: DatabaseClient,
    conversation_id: Optional[str],
    user_id: Optional[str],
    top_k: int = _DEFAULT_TOP_K,
) -> List[EvidenceRef]:
    """
    从会话历史中派生 evidence refs（Top-K 文档，按 doc_id 去重取最近引用）。

    Args:
        db: 数据库客户端
        conversation_id: 会话 ID
        user_id: 用户 ID
        top_k: 返回条数，非正整数直接返回空

    Returns:
        List[EvidenceRef]: 排序后的证据列表；无证据返回空列表
    """
    if top_k <= 0:
        return []
    if not conversation_id or not user_id:
        return []

    rows = _list_recent_assistant_with_sources(db, conversation_id, user_id)
    if not rows:
        return []

    deduped = _dedupe_by_doc_id(rows)
    ranked = _rank_by_score_desc(deduped)
    return ranked[:top_k]


def _list_recent_assistant_with_sources(
    db: DatabaseClient,
    conversation_id: str,
    user_id: str,
) -> List[dict]:
    """
    查询最近若干 assistant 消息（含 sources JSONB 列）。
    返回按消息 id DESC（新→旧），便于后续"取每个 doc 最新一次"直接首见即可。
    """
    return db.select_rows(
        "t_message",
        columns=["id", "sources"],
        where=[
            Condition.eq("conversation_id", conversation_id),
            Condition.eq("user_id", user_id),
            Condition.eq("role", "assistant"),
            Condition.eq("deleted", 0),
        ],
        order_by=[("id", "desc")],
        limit=_SCAN_RECENT_ASSISTANT_MESSAGES,
    )


def _dedupe_by_doc_id(rows: List[dict]) -> List[EvidenceRef]:
    """
    消息按 id DESC 顺序遍历，每个 doc_id 首次出现即为该文档"最近一次引用"。
    同一条消息内 sources 按 index 升序读入（等价于原始排序意图）。
    """
    seen: Dict[str, EvidenceRef] = {}
    for row in rows:
        refs = _parse_sources_json(row.get("sources"))
        # 单消息内 index 小者优先——按 index 升序排
        refs_sorted = sorted(refs, key=lambda r: r[0])
        for index, doc_id, doc_name in refs_sorted:
            if not doc_id:
                continue
            if doc_id in seen:
                continue
            seen[doc_id] = EvidenceRef(
                doc_id=doc_id,
                doc_name=doc_name,
                # score: 用 index 归一化（index 越小越相关）；Phase 7 换成真实 rerank_score
                score=_index_to_score(index),
            )
    return list(seen.values())


def _parse_sources_json(raw: Any) -> List[tuple]:
    """
    解析 t_message.sources 列（JSONB）→ [(index, doc_id, doc_name), ...]
    非 JSON 字符串 / 非列表 / 缺字段 → 视为空。
    in-memory 后端可能直接给 dict，SQL 后端可能给 str——两种都吃。
    """
    if raw is None:
        return []
    data: Any
    if isinstance(raw, (list, tuple)):
        data = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("t_message.sources 非合法 JSON，忽略")
            return []
    else:
        return []

    if not isinstance(data, list):
        return []

    result: List[tuple] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        doc_id = item.get("docId") or item.get("doc_id")
        if not doc_id or not str(doc_id).strip():
            continue
        doc_name = item.get("docName") or item.get("doc_name")
        raw_index = item.get("index")
        try:
            index = int(raw_index) if raw_index is not None else 999
        except (TypeError, ValueError):
            index = 999
        result.append((index, str(doc_id).strip(), doc_name))
    return result


def _index_to_score(index: int) -> float:
    """
    把 SourceRef.index（1-based）映射为 [0, 1] 单调递减分数，仅用于排序 tie-breaker。
    Phase 7 引入真实 rerank_score 后本函数移除。
    """
    if index <= 0:
        return 0.0
    return 1.0 / float(index)


def _rank_by_score_desc(refs: List[EvidenceRef]) -> List[EvidenceRef]:
    return sorted(
        refs,
        key=lambda r: (r.score if r.score is not None else 0.0),
        reverse=True,
    )
