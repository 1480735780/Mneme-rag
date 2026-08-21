# -*- coding: utf-8 -*-
"""
检索配置（对应 ragent 检索域配置）

- `ScopeProperties`：检索作用域（对应 Java `SearchChannelProperties.Scope`）——min_intent_score /
  confidence_threshold / supplement_ratio，供 RetrievalScopeResolver / 向量通道定向作用域使用。
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
