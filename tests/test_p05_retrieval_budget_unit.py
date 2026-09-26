# -*- coding: utf-8 -*-
"""
V2.1.1 P-05 单元测试：Retrieval Budget 动态估算

覆盖：
    - 冷启动：无观测样本时用默认 avg 估算
    - 系数映射：LOOKUP/EXPLAIN/SYNTHESIS 依次放大
    - record_actual 后 avg 收敛到实际观测值
    - 观测窗口按 maxlen 滚动，旧样本被丢弃
    - 非法输入（<=0）忽略
    - 最小 budget 兜底 200（防止配置错到 0 触发 Guard 死锁）
    - 配置校验 raise
    - 多线程安全（简单 smoke，验证 lock 生效）
"""
from __future__ import annotations

import threading

import pytest

from rag.memory.context.retrieval_budget import (
    QueryType,
    RetrievalBudgetConfig,
    RetrievalBudgetEstimator,
)


# ==================== 冷启动 ====================

def test_cold_start_uses_default_avg():
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(rerank_top_k=5, cold_start_avg_chunk_tokens=800)
    )
    # LOOKUP 系数 1.0 → baseline=4000
    assert estimator.compute_budget(QueryType.LOOKUP) == 4000


def test_multipliers_scale_baseline():
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(rerank_top_k=5, cold_start_avg_chunk_tokens=800)
    )
    lookup = estimator.compute_budget(QueryType.LOOKUP)
    explain = estimator.compute_budget(QueryType.EXPLAIN)
    synth = estimator.compute_budget(QueryType.SYNTHESIS)
    assert explain > lookup
    assert synth > explain
    # 具体比例：5 * 800 * 1.2 = 4800, 5 * 800 * 1.5 = 6000
    assert explain == 4800
    assert synth == 6000


def test_unknown_query_type_falls_back_to_explain_multiplier():
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(rerank_top_k=5, cold_start_avg_chunk_tokens=800)
    )
    unknown = estimator.compute_budget(QueryType.UNKNOWN)
    explain = estimator.compute_budget(QueryType.EXPLAIN)
    assert unknown == explain


# ==================== 观测记录 ====================

def test_record_actual_adjusts_avg():
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(rerank_top_k=5, cold_start_avg_chunk_tokens=800)
    )
    # 记录一次真实值 2500 tokens / top_k=5 = 500 per chunk
    estimator.record_actual(2500)
    # 冷启动被覆盖 → 平均就是 500
    assert estimator.compute_budget(QueryType.LOOKUP) == 5 * 500


def test_multiple_records_averages():
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(rerank_top_k=5, cold_start_avg_chunk_tokens=800)
    )
    estimator.record_actual(2500)  # per chunk 500
    estimator.record_actual(5000)  # per chunk 1000
    # 平均 750
    assert estimator.compute_budget(QueryType.LOOKUP) == 5 * 750


def test_observation_window_rolls():
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(
            rerank_top_k=1, cold_start_avg_chunk_tokens=100, observation_window=3
        )
    )
    # 灌 5 次，只有最近 3 次有效（每次 / top_k=1 = 自身值）
    for v in [100, 100, 100, 100, 900]:
        estimator.record_actual(v)
    # 最近 3 次: [100, 100, 900] → avg 366 (向下取整)
    assert estimator.observed_sample_count() == 3
    assert estimator.compute_budget(QueryType.LOOKUP) == 366


def test_invalid_record_ignored():
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(rerank_top_k=5, cold_start_avg_chunk_tokens=800)
    )
    estimator.record_actual(0)
    estimator.record_actual(-100)
    assert estimator.observed_sample_count() == 0
    # 仍是冷启动
    assert estimator.compute_budget(QueryType.LOOKUP) == 4000


# ==================== 边界 ====================

def test_minimum_budget_floor():
    """系数与 top_k 都被配到极小时也要 >= 200"""
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(
            rerank_top_k=1,
            cold_start_avg_chunk_tokens=50,
            query_type_multiplier={
                QueryType.LOOKUP: 0.5,
                QueryType.EXPLAIN: 0.5,
                QueryType.SYNTHESIS: 0.5,
                QueryType.UNKNOWN: 0.5,
            },
        )
    )
    assert estimator.compute_budget(QueryType.LOOKUP) == 200


# ==================== 配置校验 ====================

def test_invalid_rerank_top_k_raises():
    with pytest.raises(ValueError):
        RetrievalBudgetConfig(rerank_top_k=0)


def test_invalid_observation_window_raises():
    with pytest.raises(ValueError):
        RetrievalBudgetConfig(observation_window=0)


def test_invalid_multiplier_value_raises():
    with pytest.raises(ValueError):
        RetrievalBudgetConfig(
            query_type_multiplier={QueryType.LOOKUP: 0}
        )


def test_invalid_multiplier_key_raises():
    with pytest.raises(ValueError):
        RetrievalBudgetConfig(
            query_type_multiplier={"lookup": 1.0}
        )


# ==================== 线程安全 ====================

def test_concurrent_record_and_read_no_corruption():
    """多线程 record + compute 不崩，最终样本数 <= 窗口"""
    estimator = RetrievalBudgetEstimator(
        RetrievalBudgetConfig(
            rerank_top_k=1, cold_start_avg_chunk_tokens=100, observation_window=50
        )
    )

    def worker(v: int) -> None:
        for _ in range(30):
            estimator.record_actual(v)
            _ = estimator.compute_budget(QueryType.EXPLAIN)

    threads = [threading.Thread(target=worker, args=(v,)) for v in [200, 400, 800]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 观测窗口按 maxlen 保留 <= 50
    assert estimator.observed_sample_count() <= 50
    # 计算不抛
    assert estimator.compute_budget(QueryType.LOOKUP) > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
