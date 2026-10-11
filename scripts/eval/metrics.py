# -*- coding: utf-8 -*-
"""
scripts.eval.metrics - 检索评测指标（HitRate@k / MRR@k / NDCG@k / Intent@1 / 证据可读性）

纯函数、零依赖（除同包 chunk_metrics 的答案片段判定）：输入「检索结果 doc id 有序列表」
与「参考相关 doc id 集合」→ 单问指标；aggregate 汇总数据集级均值。
口径对齐 docs/rag/eval-guide.md（retrievedDocIds 为端点已剥后缀的业务码）。

证据可读性（evidence_metrics）是 M4 为 Parent-Child 加的：doc 级 HitRate/MRR/NDCG 只看
「命中了哪些文档」，父块回填只改证据文本、不改命中集合，这三项指标对父子块完全钝感，
必须另量「期望答案片段在证据文本里是否完整」才能看出收益。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set

from scripts.eval.chunk_metrics import answer_intact, normalize_ws


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


def evidence_metrics(contexts: Sequence[str], spans: Sequence[str]) -> Dict:
    """
    证据可读性：证据条数/字符数 + 答案片段的完整率与覆盖率

    两个答案口径分工不同，别混读：
        answer_intact_rate  = 片段完整落在**某一条**证据内。这是 Parent-Child 回填的目标指标：
                              Child 召回时答案常被切散在两条证据里，父块把整节合成一条，片段重新变完整，
                              模型不必自己跨条拼接。口径与 chunk_metrics.answer_intact 一致。
        answer_coverage_rate = 片段被证据集覆盖（跨条拼接后仍出现即算覆盖）。它量的是「答案信息有没有
                              进上下文」，与召回质量同向；parent-child 不改命中集合，这项应基本不动，
                              若它掉了则说明回填把某些召回单元挤掉了。
    """
    texts = [str(c) for c in contexts if c]
    result = {"evidence_count": len(texts), "evidence_chars": sum(len(c) for c in texts)}
    # 先滤掉空白片段再判：answer_intact 会按传入条数当分母，混入空白项会让
    # answer_total 与 intact_rate 的分母不是同一个集合（覆盖率同理）。
    clean = [s for s in spans if str(s).strip()]
    total = len(clean)
    if total == 0:
        return result
    intact = answer_intact(clean, texts)
    joined = "\n".join(normalize_ws(c) for c in texts)
    covered = sum(1 for s in clean if normalize_ws(str(s)) in joined)
    return {
        **result,
        "answer_total": total,
        "answer_intact_rate": intact["intact_rate"],
        "answer_split": intact["split"],
        "answer_missing": intact["missing"],
        "answer_coverage_rate": covered / total,
    }


def compute_metrics(record: Dict, eval_data: Dict, top_k: int) -> Dict:
    """单问指标：record={question, reference_doc_ids, intent_l2, answer_spans?} × eval_data（端点 Result.data）"""
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
        **evidence_metrics(eval_data.get("retrievedContexts") or [], record.get("answer_spans") or []),
    }


def aggregate(per_question: List[Dict], top_k: int) -> Dict:
    """
    数据集级汇总：各指标均值 + 平均延迟 + 证据可读性均值

    证据类均值只在有该字段的题上取平均：answer_* 只对带 QA 标注的题算（缺标注的题不该拉低分母），
    evidence_* 对全部题算（无标注也有证据文本）。
    """
    keys = ("hit_rate@k", "mrr@k", "ndcg@k", "intent_top1_acc")
    n = len(per_question)
    if n == 0:
        return {"count": 0, **{k: 0.0 for k in keys}, "latency_avg_ms": 0.0}
    means = {k: sum(q[k] for q in per_question) / n for k in keys}
    report = {
        "count": n,
        **means,
        "latency_avg_ms": sum(q["latency_ms"] for q in per_question) / n,
    }
    evidence_items = [q for q in per_question if "evidence_count" in q]
    if evidence_items:
        m = len(evidence_items)
        report["evidence_count_avg"] = sum(q["evidence_count"] for q in evidence_items) / m
        report["evidence_chars_avg"] = sum(q["evidence_chars"] for q in evidence_items) / m
    answer_items = [q for q in per_question if q.get("answer_total")]
    if answer_items:
        report["answer_count"] = sum(q["answer_total"] for q in answer_items)
        report["answer_intact_rate"] = (
            sum(q["answer_intact_rate"] for q in answer_items) / len(answer_items)
        )
        report["answer_coverage_rate"] = (
            sum(q["answer_coverage_rate"] for q in answer_items) / len(answer_items)
        )
    return report
