# -*- coding: utf-8 -*-
"""
检索配置（对应 ragent 检索域配置）

- `ScopeProperties`：检索作用域（对应 Java `SearchChannelProperties.Scope`）——min_intent_score /
  confidence_threshold / supplement_ratio，供 RetrievalScopeResolver / 向量通道定向作用域使用。
- `RerankProperties`：精排开关（对应 Java `rag.rerank.enabled`）——RAGENT_RERANK_ENABLED，
  控制精排链路是否接入检索处理链。
- `EvidenceProperties`：证据闸门（对应 Java `SearchChannelProperties.Evidence`）——min_rerank_score，
  整批最高精排分低于下限即丢弃全部证据（0 = 关闭）。
- `RetrievalProperties`：检索通道启停（快赢①：检索通道按配置展开）——env 驱动开关：
    - 向量   RAGENT_RETRIEVAL_VECTOR    （on/1/true/yes；读侧为 storage.vector 实现，Milvus/Pg 由 P6 接线）
    - 关键词 RAGENT_RETRIEVAL_KEYWORD   （同上；ES 后端经 rag.keyword.config.EsProperties 默认连接，未配置则内存版）
    - 图谱   RAGENT_RETRIEVAL_GRAPH     （同上；LightRAG 基址/密钥经 RAGENT_LIGHTRAG_URL / RAGENT_LIGHTRAG_API_KEY）
    - 联网   RAGENT_RETRIEVAL_WEB       （同上；API Key 经 YDC_API_KEY）
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeProperties:
    """检索作用域配置（对齐 Java SearchChannelProperties.Scope）"""

    min_intent_score: float = 0.4  # 命中意图最低分数（低于则不算命中）
    confidence_threshold: float = 0.6  # 置信收窄阈值（高于则定向）
    supplement_ratio: float = 0.25  # 划给补充路的证据比例


def _flag(name: str) -> bool:
    """env 开关解析：1/true/on/yes 视为启用（其余视为关闭）"""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "on", "yes"}


@dataclass(frozen=True)
class RetrievalProperties:
    """检索通道启停配置（快赢①：检索通道按配置展开）"""

    vector_enabled: bool = False
    keyword_enabled: bool = False
    graph_enabled: bool = False
    web_search_enabled: bool = False
    # 图谱后端（LightRAG）：基址 / API Key
    lightrag_url: str = "http://127.0.0.1:9621"
    lightrag_api_key: str = ""
    # 联网后端（You.com）：配置优先，回退 env YDC_API_KEY（通道自身也会回退）
    web_api_key: str = ""

    @classmethod
    def from_env(cls) -> "RetrievalProperties":
        return cls(
            vector_enabled=_flag("RAGENT_RETRIEVAL_VECTOR"),
            keyword_enabled=_flag("RAGENT_RETRIEVAL_KEYWORD"),
            graph_enabled=_flag("RAGENT_RETRIEVAL_GRAPH"),
            web_search_enabled=_flag("RAGENT_RETRIEVAL_WEB"),
            lightrag_url=os.environ.get("RAGENT_LIGHTRAG_URL", "http://127.0.0.1:9621"),
            lightrag_api_key=os.environ.get("RAGENT_LIGHTRAG_API_KEY", ""),
            web_api_key=os.environ.get("YDC_API_KEY", ""),
        )


@dataclass(frozen=True)
class RerankProperties:
    """
    精排开关配置（对齐 Java rag.rerank.enabled，bootstrap application.yaml 默认 true）

    Python 默认偏离为 False：默认部署无 SILICONFLOW_API_KEY，无可用 rerank 客户端，
    开着只会让 RerankPostProcessor 每次检索空转异常；配好 ai.yaml rerank 组与 key 后
    置 RAGENT_RERANK_ENABLED=on 激活（闸门 min-rerank-score 才有分可读）。
    """

    enabled: bool = False

    @classmethod
    def from_env(cls) -> "RerankProperties":
        return cls(enabled=_flag("RAGENT_RERANK_ENABLED"))


@dataclass(frozen=True)
class EvidenceProperties:
    """
    证据闸门配置（对齐 Java SearchChannelProperties.Evidence）

    env：RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE（对应 Java rag.search.evidence.min-rerank-score）。
    0 = 关闭闸门。

    注意默认值偏离：Java 默认 0.2（其精排链默认在链上）；Python 侧精排链路尚未接入生产装配
    （RerankPostProcessor / RoutingRerankService 未接线），默认 0.2 会立刻触发闸门启动校验，
    故默认 0（关闭），精排接线后可按 Java 语义调回 0.2。
    """

    min_rerank_score: float = 0.0

    def __post_init__(self):
        # 范围校验（对齐 Java SearchChannelProperties.Evidence 的 setter 校验：NaN 或 >1 报错）
        import math

        if math.isnan(self.min_rerank_score) or self.min_rerank_score > 1:
            raise ValueError(f"evidence.min-rerank-score 非法: {self.min_rerank_score}（须为 [0, 1] 内的数，0 = 关闭）")

    @classmethod
    def from_env(cls) -> "EvidenceProperties":
        raw = os.environ.get("RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE", "").strip()
        if not raw:
            return cls()
        try:
            return cls(min_rerank_score=float(raw))
        except ValueError:
            raise ValueError(f"RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE 非法: {raw!r}（须为数值，0 = 关闭）") from None
