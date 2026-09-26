# -*- coding: utf-8 -*-
"""
rag.memory.context.retrieval_budget - Retrieval 预算动态估算（V2.1.1 / P-05）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 5 节

Phase 1 Pre-Retrieval Guard 需要预留 retrieval_budget，用于计算：
    history_budget = context_window
                   - system - query - retrieval - mcp - output_reserve - safety

V2.1 原稿把 retrieval_budget 当常数（比如 2500 / 6000），会导致：
    设小了：装配完仍超（触发 P-04 L3 降级，但已经浪费一次 LLM 请求规划）
    设大了：history_budget 被压死，摘要与 recent raw 装不下

P-05 决策：retrieval_budget 走"静态基线 × query 类型系数"两阶段：
    baseline = rerank_top_k × estimated_avg_chunk_tokens
    budget   = baseline × query_type_multiplier[query_type]

其中 estimated_avg_chunk_tokens 从最近 N 次真实检索返回的平均值滚动统计，
冷启动默认 800。P-04 L3 触发时把 actual_retrieval_tokens 回写本模块，形成观测闭环。

**当前实现状态**：只建数据结构与滚动统计器，不与 Pre-Retrieval Guard 集成
（Pre-Retrieval Guard 属于 Phase 1，等其落地时直接调 compute_budget 即可）。
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Optional

# 冷启动时"平均 chunk token 数"的默认估算，中英文混合场景的经验值
_DEFAULT_AVG_CHUNK_TOKENS_COLD = 800

# 观测窗口：滚动统计最近 N 次真实检索大小（默认 500 条足够覆盖典型部署）
_DEFAULT_OBSERVATION_WINDOW = 500


class QueryType(Enum):
    """
    Query 类型（对应意图分类结果的粗粒度映射；Phase 1 意图三级级联完成后填入）

    分类含义：
        LOOKUP    简单事实型，命中一个 chunk 就够
        EXPLAIN   概念解释型，需要 2-3 个 chunk 组织
        SYNTHESIS 多文档综合型，需要 5+ 个 chunk 拼接
        UNKNOWN   未分类（回落 EXPLAIN 系数）
    """

    LOOKUP = "lookup"
    EXPLAIN = "explain"
    SYNTHESIS = "synthesis"
    UNKNOWN = "unknown"


@dataclass
class RetrievalBudgetConfig:
    """
    Retrieval 预算配置

    Attributes:
        rerank_top_k: 精排保留条数
        query_type_multiplier: 每种 query_type 的 budget 系数
        cold_start_avg_chunk_tokens: 冷启动时单 chunk 平均 token 数
        observation_window: 滚动观测样本数
    """

    rerank_top_k: int = 5
    query_type_multiplier: dict = field(
        default_factory=lambda: {
            QueryType.LOOKUP: 1.0,
            QueryType.EXPLAIN: 1.2,
            QueryType.SYNTHESIS: 1.5,
            QueryType.UNKNOWN: 1.2,
        }
    )
    cold_start_avg_chunk_tokens: int = _DEFAULT_AVG_CHUNK_TOKENS_COLD
    observation_window: int = _DEFAULT_OBSERVATION_WINDOW

    def __post_init__(self) -> None:
        if self.rerank_top_k <= 0:
            raise ValueError("rerank_top_k 必须为正整数")
        if self.observation_window <= 0:
            raise ValueError("observation_window 必须为正整数")
        for k, v in self.query_type_multiplier.items():
            if not isinstance(k, QueryType):
                raise ValueError(f"query_type_multiplier key 必须是 QueryType: {k!r}")
            if v <= 0:
                raise ValueError(f"{k} 系数必须为正: {v}")


class RetrievalBudgetEstimator:
    """
    滚动观测 + 分类型预算估算器。

    用法：
        estimator = RetrievalBudgetEstimator(RetrievalBudgetConfig(rerank_top_k=5))
        budget = estimator.compute_budget(QueryType.SYNTHESIS)
        ...
        # Phase 1 P-04 L3 回写实际观测值时：
        estimator.record_actual(actual_tokens=3800)
    """

    def __init__(self, config: Optional[RetrievalBudgetConfig] = None) -> None:
        self._config = config or RetrievalBudgetConfig()
        self._samples: Deque[int] = deque(maxlen=self._config.observation_window)
        self._lock = threading.Lock()

    @property
    def config(self) -> RetrievalBudgetConfig:
        return self._config

    def compute_budget(self, query_type: QueryType) -> int:
        """
        给定 query 类型返回推荐的 retrieval_budget（token 数）。

        公式：baseline = rerank_top_k × avg_chunk_tokens_estimate
             budget   = baseline × multiplier[query_type]
             最终取整且不低于最小 200（避免"算出 0 budget" 触发 Guard 死锁）
        """
        avg = self._avg_chunk_tokens()
        baseline = self._config.rerank_top_k * avg
        multiplier = self._config.query_type_multiplier.get(query_type, 1.2)
        return max(200, int(baseline * multiplier))

    def record_actual(self, actual_tokens: int) -> None:
        """
        记录一次真实检索返回的总 token 数。观测窗口按样本数滚动，非时间窗口。
        实际值除以 top_k 得到"本次单 chunk token"，进入 avg 池。
        非正数直接忽略（防止错误记录污染统计）。
        """
        if actual_tokens <= 0:
            return
        per_chunk = max(1, actual_tokens // max(1, self._config.rerank_top_k))
        with self._lock:
            self._samples.append(per_chunk)

    def observed_sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def _avg_chunk_tokens(self) -> int:
        with self._lock:
            if not self._samples:
                return self._config.cold_start_avg_chunk_tokens
            return sum(self._samples) // len(self._samples)


__all__ = [
    "QueryType",
    "RetrievalBudgetConfig",
    "RetrievalBudgetEstimator",
]
