# -*- coding: utf-8 -*-
"""
core.llm.model.selector - 模型选择器（对应 ragent 的 ModelSelector）

本模块负责根据 ai.yaml 配置与运行时健康状态，将抽象的业务需求
（thinking 标志、Tier 档位、preferred_model_id）映射为有序、可用、
携带完整路由元数据的 ModelTarget 候选列表，供 RoutingExecutor 逐个尝试。

架构对应关系：
    Ragent (Java)                          Mneme-rag (Python)
    ──────────────────────────────────────────────────
    infra/model/ModelSelector.java     --> core/llm/model/selector.py (ModelSelector)
    infra/config/AIModelProperties     --> core/llm/config/config.py (AIModelConfig)
    infra/model/ModelTarget            --> core/llm/model/model_target.py (ModelTarget)
    infra/model/ModelHealthStore       --> core/llm/model/health_store.py

核心流程：
    确定 Tier → 获取候选 → 基于启用状态与健康度过滤 → 构建 ModelTarget

两套并行的选择机制（对齐 Java 实现）：
    1. chat 组走档位机制：任务 → 档位（tier）→ 档位内有序候选，
       超时预算随档位下沉到每个 ModelTarget；
    2. embedding / rerank / vlm 组走 default_model + priority 的传统排序。

设计说明：
    - 本类为纯逻辑组件：不发起任何网络调用，只做"筛选 + 排序 + 组装"，可复用；
    - 健康检查前置到选择阶段：熔断中的模型直接从候选列表消失，
      避免 RoutingExecutor 对无效目标发起调用（执行期仍有 allow_call 双保险）；
    - 所有配置错误只告警不中断（fail-soft），保证系统可用性的同时
      通过结构化日志暴露配置漂移。
"""

import logging
from typing import Any, Dict, List, Optional

from ..config.config import AIModelConfig, ModelCandidate, ModelGroup, ProviderConfig
from .model_target import ModelTarget

logger = logging.getLogger(__name__)


class ModelSelector:
    """
    模型选择器（对应 Java 的 ModelSelector）。

    负责根据配置和当前需求选择合适的模型候选列表。chat 组走档位机制，
    embedding / rerank / vlm 组走 default_model + priority 的传统排序。

    使用示例：
        selector = ModelSelector(config, health_store)

        # 默认档位
        targets = selector.select_chat_candidates(thinking=False)

        # 显式档位 + 优先模型
        targets = selector.select_chat_candidates(
            thinking=True, override="deep", preferred_model_id="qwen-max"
        )
    """

    def __init__(self, properties: AIModelConfig, health_store: Any = None) -> None:
        """
        初始化模型选择器。

        Args:
            properties: AI 模型总配置（对应 Java 的 AIModelProperties），
                由 ai.yaml 加载得到。
            health_store: 模型健康状态存储（对应 Java 的 ModelHealthStore），
                需对外提供 is_unavailable(model_id: str) -> bool 方法；
                为 None 时跳过健康度过滤，退化为纯配置驱动选择。
        """
        self._properties = properties
        self._health_store = health_store

    # ==================== 公共 API ====================

    def select_chat_candidates(
        self,
        thinking: bool,
        override: Optional[Any] = None,
        preferred_model_id: Optional[str] = None,
    ) -> List[ModelTarget]:
        """
        选择 chat 候选（合并 Java 的三个重载为默认参数形式）。

        档位解析优先级：深度思考档（deep_thinking_tier）> 显式 override > 默认档（default_tier）。
        preferred 语义：非空时优先该模型，失败后回退到解析出的档位的其余候选。

        Args:
            thinking: 是否深度思考请求；True 且配置了 deep_thinking_tier 时
                命中深度思考档位，并过滤掉不支持思考链的候选（含 preferred）。
            override: 显式档位覆盖（对应 Java 的 Tier 枚举）；可直接传档位名
                字符串，或具有 key 属性的 Tier 枚举（取 override.key）。
            preferred_model_id: 优先模型 id，为空时等同于无 preferred。

        Returns:
            List[ModelTarget]: 有序候选列表；chat 组未配置时返回空列表。
            
        函数设计思路：
        def select_chat_candidates():

            1. 确定tier

            2. 获取tier对应candidate id列表

            3. 根据id找到ModelCandidate

            4. enabled过滤

            5. thinking能力过滤

            6. priority排序

            7. 绑定ProviderConfig

            8. 创建ModelTarget

            return List[ModelTarget]
        """
        group = self._properties.chat
        if group is None:
            return []
        tier_name = self._resolve_tier_name(group, thinking, override)
        # 用户请求思考时，路由与 preferred 都必须过滤掉不支持思考的模型
        return self._build_tier_targets(group, tier_name, preferred_model_id, thinking)

    def select_embedding_candidates(self) -> List[ModelTarget]:
        """选择 embedding 候选（default_model 置顶 + priority 升序）。"""
        return self._select_candidates(self._properties.embedding)

    def select_rerank_candidates(self) -> List[ModelTarget]:
        """选择 rerank 候选（default_model 置顶 + priority 升序）。"""
        return self._select_candidates(self._properties.rerank)

    def select_vlm_candidates(self) -> List[ModelTarget]:
        """选择 vlm 候选（default_model 置顶 + priority 升序，图生文入库期使用）。"""
        return self._select_candidates(self._properties.vlm)

    # ==================== chat：档位机制 ====================

    def _resolve_tier_name(
        self,
        group: ModelGroup,
        thinking: bool,
        override: Optional[Any],
    ) -> Optional[str]:
        """
        档位解析：深度思考档 > 显式覆盖 > 默认档。

        深度思考优先于显式覆盖：即使调用方传了 override，只要 thinking=True
        且配置了 deep_thinking_tier，就走深度思考档位（thinking 是用户硬性
        需求，override 只是调用方的性能偏好）。
        """
        if thinking and group.deep_thinking_tier and group.deep_thinking_tier.strip():
            return group.deep_thinking_tier
        if override is not None:
            # 兼容 Tier 枚举（取 .key）与纯字符串两种入参
            return getattr(override, "key", override)
        return group.default_tier

    def _build_tier_targets(
        self,
        group: ModelGroup,
        tier_name: Optional[str],
        preferred_model_id: Optional[str],
        require_thinking: bool,
    ) -> List[ModelTarget]:
        """
        按档位构造有序候选：preferred 置队首，随后拼接档位候选（去重），
        逐个过滤未启用 / 不支持思考 / 不健康 / 未登记的候选；
        命中的档位超时预算随每个 target 下沉。

        Args:
            group: chat 模型组配置。
            tier_name: 解析后的档位名。
            preferred_model_id: 优先模型 id（可为空）。
            require_thinking: 是否要求候选支持思考链。
        """
        registry = self._build_registry(group.candidates)

        ordered_ids: List[str] = []
        if preferred_model_id and preferred_model_id.strip():
            preferred = registry.get(preferred_model_id)
            if preferred is None:
                logger.warning(
                    "Chat preferred 模型未在注册表登记，忽略并回退档位候选: "
                    "preferred_model_id=%s",
                    preferred_model_id,
                )
            elif require_thinking and not self._supports_thinking(preferred):
                logger.warning(
                    "Chat preferred 模型不支持思考，思考请求下忽略: "
                    "preferred_model_id=%s",
                    preferred_model_id,
                )
            else:
                ordered_ids.append(preferred_model_id)

        tier = group.tiers.get(tier_name) if group.tiers else None
        timeout_ms = tier.timeout_ms if tier is not None else None
        if tier is None:
            logger.warning("Chat 档位配置缺失: tier=%s", tier_name)
        else:
            for model_id in tier.candidates:
                if model_id not in ordered_ids:
                    ordered_ids.append(model_id)

        providers = self._properties.providers
        targets: List[ModelTarget] = []
        for model_id in ordered_ids:
            candidate = registry.get(model_id)
            if candidate is None:
                logger.warning(
                    "Chat 档位候选 id 未在注册表登记: id=%s, tier=%s",
                    model_id,
                    tier_name,
                )
                continue
            # 仅显式禁用（enabled is False）才过滤，缺省视为启用
            if candidate.enabled is False:
                continue
            # 思考请求下剔除不支持思考链的候选，避免思考请求被路由到普通模型
            if require_thinking and not self._supports_thinking(candidate):
                continue
            target = self._build_model_target(candidate, providers, timeout_ms)
            if target is not None:
                targets.append(target)
        return targets

    @staticmethod
    def _supports_thinking(candidate: ModelCandidate) -> bool:
        """判断候选是否支持思考链（对应 Java supportsThinking，仅 True 视为支持）。"""
        return candidate.supports_thinking is True

    @staticmethod
    def _build_registry(
        candidates: Optional[List[ModelCandidate]],
    ) -> Dict[str, ModelCandidate]:
        """
        构建 id -> 候选 注册表（dict 保留插入顺序，对应 Java LinkedHashMap）。

        跳过空列表与空元素；id 缺省时回退 "provider::model" 复合键。
        """
        registry: Dict[str, ModelCandidate] = {}
        if not candidates:
            return registry
        for candidate in candidates:
            if candidate is not None:
                registry[ModelSelector._resolve_id(candidate)] = candidate
        return registry

    # ==================== embedding/rerank/vlm：default_model + priority ====================

    def _select_candidates(self, group: Optional[ModelGroup]) -> List[ModelTarget]:
        """传统排序入口：过滤排序后组装为可用目标。"""
        if group is None or not group.candidates:
            return []
        ordered = self._filter_and_sort_candidates(group.candidates, group.default_model)
        return self._build_available_targets(ordered)

    def _filter_and_sort_candidates(
        self,
        candidates: List[ModelCandidate],
        first_choice_model_id: Optional[str],
    ) -> List[ModelCandidate]:
        """
        过滤并排序候选模型列表：首选模型置顶，其余按 priority、id 排序。

        排序键（优先级从高到低，对应 Java 的三级 Comparator）：
            1. 首选模型（default_model）置顶；
            2. priority 升序（数值越小优先级越高，None 排最后）；
            3. id 字典序兜底（None 排最后，保证排序稳定可预测）。
        """

        def sort_key(candidate: ModelCandidate) -> tuple:
            is_not_first = self._resolve_id(candidate) != first_choice_model_id
            # None 排最后：None -> (1, 兜底值)，有值 -> (0, 实际值)
            priority_key = (1, 0) if candidate.priority is None else (0, candidate.priority)
            id_key = (1, "") if not candidate.id else (0, candidate.id)
            return (is_not_first, priority_key, id_key)

        return sorted(
            (c for c in candidates if c is not None and c.enabled is not False),
            key=sort_key,
        )

    def _build_available_targets(
        self,
        candidates: List[ModelCandidate],
    ) -> List[ModelTarget]:
        """
        组装可用目标列表。

        embedding / rerank / vlm 无档位预算，timeout_ms 传 None，
        超时走 HTTP 客户端默认。
        """
        providers = self._properties.providers
        targets: List[ModelTarget] = []
        for candidate in candidates:
            target = self._build_model_target(candidate, providers, None)
            if target is not None:
                targets.append(target)
        return targets

    # ==================== 通用 ====================

    def _build_model_target(
        self,
        candidate: ModelCandidate,
        providers: Dict[str, ProviderConfig],
        timeout_ms: Optional[int],
    ) -> Optional[ModelTarget]:
        """
        候选 → 目标的最后一步：健康度过滤 + Provider 配置校验。

        健康检查前置到选择阶段（对应 Java healthStore.isUnavailable）：
        熔断中的模型直接返回 None，从候选列表消失，避免对无效目标发起调用。

        Returns:
            ModelTarget: 校验通过的目标对象；不健康或 Provider 配置缺失时返回 None。
        """
        model_id = self._resolve_id(candidate)

        # 健康度过滤：未注入 health_store 时跳过，退化为纯配置驱动
        if self._health_store is not None and self._health_store.is_unavailable(model_id):
            return None

        provider = providers.get(candidate.provider)
        # NOOP 空实现提供商允许配置缺失（对应 Java ModelProvider.NOOP.matches）
        if provider is None and not self._is_noop_provider(candidate.provider):
            logger.warning(
                "Provider配置缺失: provider=%s, model_id=%s",
                candidate.provider,
                model_id,
            )
            return None

        return ModelTarget(
            id=model_id,
            candidate=candidate,
            provider=provider,
            timeout_ms=timeout_ms,
        )

    @staticmethod
    def _is_noop_provider(provider: Optional[str]) -> bool:
        """判断是否为 NOOP 空实现提供商（对应 equalsIgnoreCase 语义，忽略大小写）。"""
        return provider is not None and provider.lower() == "noop"

    @staticmethod
    def _resolve_id(candidate: ModelCandidate) -> str:
        """
        解析模型唯一标识：显式 id 优先，缺省时回退 "provider::model" 复合键，
        保证无 id 配置时也能唯一标识（对应 Java resolveId）。
        """
        if candidate.id and candidate.id.strip():
            return candidate.id
        provider = candidate.provider if candidate.provider else "unknown"
        model = candidate.model if candidate.model else "unknown"
        return f"{provider}::{model}"
