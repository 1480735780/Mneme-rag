# -*- coding: utf-8 -*-
"""
scripts.eval.metrics - 检索评测指标（HitRate@k / MRR@k / NDCG@k / Intent@1）

纯函数、零依赖：输入「检索结果 doc id 有序列表」与「参考相关 doc id 集合」→ 单问指标；
aggregate 汇总数据集级均值。口径对齐 docs/rag/eval-guide.md（retrievedDocIds 为端点已剥后缀的业务码）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Set


def hit_rate_at_k(relevant: Set[str], retrieved: List[str], k: int) -> float:
    """top-k 内是否命中任一相关 doc（0.0/1.0）；参考集为空 → 0.0"""
    if not relevant:
        return 0.0
    return 1.0 if any(doc in relevant for doc in retrieved[:k]) else 0.0


def mrr_at_k(relevant: Set[str], retrieved: List[str], k: int) -> float:
    """首个相关 doc 的倒数排名（1-based）；top-k 内无命中 → 0.0"""
    for i, doc in enumerate(retrieved[:k], start=1):
        if doc in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(relevant: Set[str], retrieved: List[str], k: int) -> float:
    """二进制相关 NDCG@k：DCG = Σ rel_p / log2(p+1)；IDCG 为相关项全置顶的理想排序
    参考集为空或检索为空 → 0.0"""
    rel = [1.0 if doc in relevant else 0.0 for doc in retrieved[:k]]
    dcg = sum(r / math.log2(p + 1) for p, r in enumerate(rel, start=1))
    idcg = sum(1.0 / math.log2(p + 1) for p in range(1, min(k, len(relevant)) + 1))
    return dcg / idcg if idcg > 0 else 0.0


def intent_top1_accuracy(expected: Optional[str], actual: Optional[str]) -> float:
    """Top-1 意图准确率：期望/实际均非空且相等 → 1.0，否则 0.0（含任一方为 None）"""
    if not expected or not actual:
        return 0.0
    return 1.0 if expected == actual else 0.0


def compute_metrics(record: Dict, eval_data: Dict, top_k: int) -> Dict:
    """单问指标：record={question, reference_doc_ids, intent_l2} × eval_data（端点 Result.data）"""
    relevant = {str(d) for d in (record.get("reference_doc_ids") or [])}
    retrieved = [str(d) for d in (eval_data.get("retrievedDocIds") or [])]
    expected = record.get("intent_l2")
    actual = (eval_data.get("intentLeafIds") or [None])[0]
    return {
        "question": record["question"],
        "reference_doc_ids": sorted(relevant),
        "retrieved_doc_ids": retrieved,
        "hit_rate@k": hit_rate_at_k(relevant, retrieved, top_k),
        "mrr@k": mrr_at_k(relevant, retrieved, top_k),
        "ndcg@k": ndcg_at_k(relevant, retrieved, top_k),
        "intent_top1_acc": intent_top1_accuracy(expected, actual),
        "latency_ms": eval_data.get("latencyMs") or 0,
    }


def aggregate(per_question: List[Dict], top_k: int) -> Dict:
    """数据集级汇总：各指标均值 + 平均延迟；空列表 → count=0 且指标全 0"""
    keys = ("hit_rate@k", "mrr@k", "ndcg@k", "intent_top1_acc")
    n = len(per_question)
    if n == 0:
        return {"count": 0, **{k: 0.0 for k in keys}, "latency_avg_ms": 0.0}
    means = {k: sum(q[k] for q in per_question) / n for k in keys}
    return {
        "count": n,
        **means,
        "latency_avg_ms": sum(q["latency_ms"] for q in per_question) / n,
    }
