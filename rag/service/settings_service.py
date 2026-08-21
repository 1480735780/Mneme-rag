# -*- coding: utf-8 -*-
"""
rag.service.settings_service - 系统设置聚合 service（对应 Java RAGSettingsController.settings 的数据装配）

域职责（M5 5.6）：把分散的配置源投影为 SystemSettingsVO（snake_case），供前端 /rag/settings 只读展示：
    - upload：上传大小上限（max_file_size / max_request_size，缺省 50MB / 100MB）；
    - rag：默认检索配置（collectionName/dimension/metricType）+ queryRewrite 开关 + **引用开关（citation）**
      + rateLimit（M6 全局限流，未装配为 None）+ memory（MemoryProperties 投影）；
    - ai：提供商（apiKey **脱敏 mask**）+ chat/embedding/rerank 模型组（含 **深度思考档 deep_thinking_tier**）
      + selection 熔断 + stream 分块。

边界：演示模式（DemoMode）随 P7 平台化（§1.3 排除），本层不投影；
rateLimit 依赖 M6 配置，未注入时返回 None（随 6.x 接通）。

方案 B：本层输出 snake_case dict，camelCase 序列化由 controller 边界 pydantic VO（5.7）完成。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.RAGSettingsController
    - com.nageoffer.ai.ragent.rag.controller.vo.SystemSettingsVO
    - com.nageoffer.ai.ragent.rag.config.RAGConfigProperties / RAGDefaultProperties / RAGRateLimitProperties
    - com.nageoffer.ai.ragent.rag.config.MemoryProperties / infra.config.AIModelProperties
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from core.llm.config.config import AIModelConfig, ModelCandidate, ModelGroup, ProviderConfig
from rag.memory.config import MemoryProperties
from rag.service.ratelimit.config import RateLimitProperties

# 上传大小缺省（对齐 Java @Value 缺省：50MB / 100MB）
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_REQUEST_SIZE = 100 * 1024 * 1024


@dataclass(frozen=True)
class RagDefaultConfig:
    """默认检索配置（对齐 Java RAGDefaultProperties）"""

    collection_name: Optional[str] = None
    dimension: Optional[int] = None
    metric_type: Optional[str] = "cosine"


class SystemSettingsService:
    """系统设置聚合服务（对应 Java RAGSettingsController 的数据装配逻辑）"""

    def __init__(
        self,
        *,
        memory_properties: Optional[MemoryProperties] = None,
        ai_config: Optional[AIModelConfig] = None,
        query_rewrite_enabled: bool = True,
        citation_enabled: bool = False,
        rag_default: Optional[RagDefaultConfig] = None,
        rate_limit: Optional[RateLimitProperties] = None,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_request_size: int = DEFAULT_MAX_REQUEST_SIZE,
        orchestration_mode: str = "workflow",
    ):
        self._memory = memory_properties or MemoryProperties()
        self._ai = ai_config
        self._query_rewrite_enabled = query_rewrite_enabled
        self._citation_enabled = citation_enabled
        self._rag_default = rag_default or RagDefaultConfig()
        self._rate_limit = rate_limit
        self._max_file_size = max_file_size
        self._max_request_size = max_request_size
        # 编排模式（workflow | agent，部署级）：由 AppSettings 回注，与 5.5 槽位生效集同源
        self._orchestration_mode = orchestration_mode

    def get_settings(self) -> Dict:
        """聚合系统设置（对齐 Java settings() 装配）"""
        return {
            "orchestration_mode": self._orchestration_mode,
            "upload": {
                "max_file_size": self._max_file_size,
                "max_request_size": self._max_request_size,
            },
            "rag": {
                "default": {
                    "collection_name": self._rag_default.collection_name,
                    "dimension": self._rag_default.dimension,
                    "metric_type": self._rag_default.metric_type,
                },
                "query_rewrite": {"enabled": self._query_rewrite_enabled},
                "citation": {"enabled": self._citation_enabled},
                "rate_limit": _to_rate_limit(self._rate_limit),
                "memory": {
                    "history_keep_turns": self._memory.history_keep_turns,
                    "summary_enabled": self._memory.summary_enabled,
                    "summary_start_turns": self._memory.summary_start_turns,
                    "summary_max_chars": self._memory.summary_max_chars,
                    "title_max_length": self._memory.title_max_length,
                },
            },
            "ai": _to_ai(self._ai),
        }


# ==================== 投影 / 工具 ====================


def _to_ai(ai_config: Optional[AIModelConfig]) -> Dict:
    """AI 配置投影（providers apiKey 脱敏；chat 含深度思考档；未注入 → 空结构）"""
    if ai_config is None:
        return {"providers": {}, "chat": None, "embedding": None, "rerank": None,
                "selection": None, "stream": None}
    providers = {
        name: {
            "url": cfg.url,
            "api_key": _mask_api_key(cfg.api_key),
            "endpoints": cfg.endpoints,
        }
        for name, cfg in ai_config.providers.items()
    }
    return {
        "providers": providers,
        "chat": _to_model_group(ai_config.chat),
        "embedding": _to_model_group(ai_config.embedding),
        "rerank": _to_model_group(ai_config.rerank),
        "selection": {
            "failure_threshold": ai_config.selection.failure_threshold,
            "open_duration_ms": ai_config.selection.open_duration_ms,
        } if ai_config.selection else None,
        "stream": {
            "message_chunk_size": ai_config.stream.message_chunk_size,
        } if ai_config.stream else None,
    }


def _to_model_group(group: Optional[ModelGroup]) -> Optional[Dict]:
    if group is None:
        return None
    return {
        "default_model": group.default_model,
        "candidates": [_to_candidate(c) for c in group.candidates],
        "default_tier": group.default_tier,
        "deep_thinking_tier": group.deep_thinking_tier,  # 深度思考档
        "tiers": {
            name: {"candidates": t.candidates, "timeout_ms": t.timeout_ms}
            for name, t in (group.tiers or {}).items()
        },
    }


def _to_candidate(c: ModelCandidate) -> Dict:
    return {
        "id": c.id,
        "provider": c.provider,
        "model": c.model,
        "url": c.url,
        "dimension": c.dimension,
        "priority": c.priority,
        "enabled": c.enabled,
        "supports_thinking": c.supports_thinking,
    }


def _to_rate_limit(p: Optional[RateLimitProperties]) -> Optional[Dict]:
    """全局限流投影；未注入返回 None。单真源：直接由 RateLimitProperties 投影（无重复默认值）。"""
    if p is None:
        return None
    return {
        "global": {
            "enabled": p.global_enabled,
            "max_concurrent": p.global_max_concurrent,
            "max_wait_seconds": p.global_max_wait_seconds,
            "lease_seconds": p.global_lease_seconds,
            "poll_interval_ms": p.global_poll_interval_ms,
        }
    }


def _mask_api_key(api_key: Optional[str]) -> Optional[str]:
    """apiKey 脱敏（对齐 Java maskApiKey）：空白→None；≤10 位→全掩；否则 前6+***+后4；
    未解析的 `${ENV}` 占位符视为未配置 → None（不展示掩码占位符）。"""
    if not api_key or not str(api_key).strip():
        return None
    trimmed = str(api_key).strip()
    if trimmed.startswith("${"):
        return None
    if len(trimmed) <= 10:
        return "******"
    return trimmed[:6] + "***" + trimmed[-4:]