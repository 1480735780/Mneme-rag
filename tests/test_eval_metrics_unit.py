# -*- coding: utf-8 -*-
"""
P1 M1 评测指标单测：scripts/eval/metrics.py（HitRate@k / MRR@k / NDCG@k / Intent@1 / aggregate）

纯内存断言（含数值固化，对齐 plan §4）：
    - hit_rate：top-k 命中/未命中/检索空/参考空/k 截断
    - mrr：首个相关 rank1/rank3/top-k 无命中/参考空
    - ndcg：理想序=1.0/部分相关按公式/参考空=0.0/检索空=0.0/k 超界不越
    - intent_top1：相等/不等/期望 None/实际 None
    - compute_metrics：字段映射 + top_k 截断
    - aggregate：多问均值/空列表/latency_avg
"""
import math

from scripts.eval.metrics import (
    aggregate,
    compute_metrics,
    hit_rate_at_k,
    intent_top1_accuracy,
    mrr_at_k,
    ndcg_at_k,
)


class TestHitRate:
    def test_hit_in_top_k(self):
        assert hit_rate_at_k({"b"}, ["a", "b", "c"], 2) == 1.0

    def test_miss(self):
        assert hit_rate_at_k({"d"}, ["a", "b", "c"], 3) == 0.0

    def test_empty_retrieved(self):
        assert hit_rate_at_k({"a"}, [], 3) == 0.0

    def test_empty_relevant(self):
        assert hit_rate_at_k(set(), ["a", "b"], 3) == 0.0

    def test_hit_beyond_k_clipped(self):
        # 命中的 doc 在第 k+1 位 → k 截断后未命中
        assert hit_rate_at_k({"c"}, ["a", "b", "c"], 2) == 0.0


class TestMRR:
    def test_first_relevant_rank1(self):
        assert mrr_at_k({"a"}, ["a", "b", "c"], 3) == 1.0

    def test_first_relevant_rank3(self):
        assert mrr_at_k({"c"}, ["a", "b", "c"], 3) == 1.0 / 3

    def test_no_relevant_in_top_k(self):
        assert mrr_at_k({"d"}, ["a", "b", "c"], 3) == 0.0

    def test_empty_relevant(self):
        assert mrr_at_k(set(), ["a", "b"], 3) == 0.0


class TestNDCG:
    def test_perfect_ordering(self):
        # 理想序：全部相关置顶 → NDCG = 1.0
        assert ndcg_at_k({"a", "b"}, ["a", "b", "c"], 3) == 1.0

    def test_partial_by_formula(self):
        # 相关 {a,b}，检索 [a,c,b]（b 在第 3 位非理想序）
        # DCG = 1/log2(2) + 0 + 1/log2(4)；IDCG = 1/log2(2) + 1/log2(3)
        dcg = 1 / math.log2(2) + 1 / math.log2(4)
        idcg = 1 / math.log2(2) + 1 / math.log2(3)
        assert math.isclose(ndcg_at_k({"a", "b"}, ["a", "c", "b"], 3), dcg / idcg)

    def test_no_relevant(self):
        assert ndcg_at_k(set(), ["a", "b", "c"], 3) == 0.0

    def test_empty_retrieved(self):
        assert ndcg_at_k({"a"}, [], 3) == 0.0

    def test_k_beyond_retrieved(self):
        # k=10 > len(retrieved)=2，不越界且相关项都在 → 1.0
        assert ndcg_at_k({"a", "b"}, ["a", "b"], 10) == 1.0


class TestIntentTop1:
    def test_match(self):
        assert intent_top1_accuracy("FAQ_VAC", "FAQ_VAC") == 1.0

    def test_mismatch(self):
        assert intent_top1_accuracy("FAQ_VAC", "FAQ_VISA") == 0.0

    def test_expected_none(self):
        assert intent_top1_accuracy(None, "FAQ_VAC") == 0.0

    def test_actual_none(self):
        assert intent_top1_accuracy("FAQ_VAC", None) == 0.0

    def test_both_none(self):
        assert intent_top1_accuracy(None, None) == 0.0


class TestComputeMetrics:
    def test_field_mapping(self):
        record = {"question": "Q1", "reference_doc_ids": ["b", "a"], "intent_l2": "FAQ_VAC"}
        eval_data = {
            "retrievedDocIds": ["a", "c"],
            "intentLeafIds": ["FAQ_VAC"],
            "latencyMs": 42,
        }
        m = compute_metrics(record, eval_data, top_k=3)
        assert m["question"] == "Q1"
        assert m["reference_doc_ids"] == ["a", "b"]  # 去重 + 排序
        assert m["retrieved_doc_ids"] == ["a", "c"]
        assert m["hit_rate@k"] == 1.0
        assert m["mrr@k"] == 1.0
        assert m["intent_top1_acc"] == 1.0
        assert m["latency_ms"] == 42

    def test_top_k_clips_metrics(self):
        record = {"question": "Q1", "reference_doc_ids": ["c"]}
        eval_data = {"retrievedDocIds": ["a", "b", "c"], "intentLeafIds": []}
        m = compute_metrics(record, eval_data, top_k=2)
        assert m["hit_rate@k"] == 0.0  # c 在第 3 位，k=2 截断
        assert m["mrr@k"] == 0.0
        assert m["intent_top1_acc"] == 0.0

    def test_intent_leaf_empty(self):
        record = {"question": "Q1", "reference_doc_ids": []}
        eval_data = {"retrievedDocIds": [], "intentLeafIds": [], "latencyMs": 0}
        m = compute_metrics(record, eval_data, top_k=3)
        assert m["intent_top1_acc"] == 0.0
        assert m["latency_ms"] == 0


class TestAggregate:
    def test_mean_across_questions(self):
        qs = [
            {"hit_rate@k": 1.0, "mrr@k": 1.0, "ndcg@k": 1.0, "intent_top1_acc": 1.0, "latency_ms": 10},
            {"hit_rate@k": 0.0, "mrr@k": 0.0, "ndcg@k": 0.0, "intent_top1_acc": 0.0, "latency_ms": 30},
        ]
        a = aggregate(qs, top_k=3)
        assert a["count"] == 2
        assert a["hit_rate@k"] == 0.5
        assert a["mrr@k"] == 0.5
        assert a["ndcg@k"] == 0.5
        assert a["intent_top1_acc"] == 0.5
        assert a["latency_avg_ms"] == 20.0

    def test_empty_list(self):
        a = aggregate([], top_k=3)
        assert a["count"] == 0
        assert a["hit_rate@k"] == 0.0
        assert a["latency_avg_ms"] == 0.0
