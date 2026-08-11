# -*- coding: utf-8 -*-
"""
core.llm.model.validator - chat 档位配置启动期校验器（对应 ragent 的 ChatTierConfigValidator）

档位机制的正确性依赖 tiers 与 candidates 注册表相互引用一致，这些错误若留到
运行期才暴露会静默降级为"档位缺失"日志。本校验器在启动时 fail-fast：
结构性错误直接抛出阻止启动，软性问题（deep 档候选未声明支持思考）仅告警。

架构对应关系：
    Ragent (Java)                                   Mneme-rag (Python)
    ────────────────────────────────────────────────────────────────
    infra/model/ChatTierConfigValidator.java  --> core/llm/model/validator.py

校验项（硬失败，抛 ValueError）：
    1. chat candidates 存在重复 id；
    2. ai.chat.tiers 未配置；
    3. default-tier / deep-thinking-tier 引用了不存在的档位；
    4. 每个档位：timeout_ms 必填且为正、候选列表非空、候选 id 必须在注册表登记；
    5. Tier 枚举的每个键都必须在 tiers 中配置；
    6. deep-thinking-tier 至少有一个「已登记 & 启用 & 支持思考」的候选。

软告警（logger.warning）：
    - deep-thinking-tier 候选未声明 supports_thinking（思考请求下将被过滤）。

用法：
    validator = ChatTierConfigValidator()
    validator.validate(config)          # 配置非法时抛 ValueError
    # 或
    validate_chat_tier_config(config)
"""

import logging
from typing import Dict, List, Optional, Set

from ..config.config import AIModelConfig, ModelCandidate, ModelGroup, TierConfig
from ..enums import Tier

logger = logging.getLogger(__name__)


class ChatTierConfigValidator:
    """
    chat 档位配置校验器（对应 Java 的 ChatTierConfigValidator）。

    对 ai.yaml 中 ai.chat 模型组的档位配置执行结构校验，保证档位引用、
    候选注册表与枚举覆盖的一致性，将潜在运行期故障提前到启动期暴露。
    """

    def validate(self, properties: AIModelConfig) -> None:
        """
        校验 chat 档位配置。

        Args:
            properties: 由 ai.yaml 加载得到的 AIModelConfig。

        Raises:
            ValueError: 存在结构性配置错误时抛出（fail-fast），
                消息汇总所有错误项，便于一次修复。
        """
        #读取chat配置
        group = properties.chat
        # 如果没用配置就直接返回
        if group is None:
            return
        # 收集错误，一次性收集所有错误，然后一次性全部修复
        errors: List[str] = []
        # 构建候选注册表（id→候选），并校验 id 唯一
        # 因为后面所有 tier 都是通过 id 引用的，所以必须先建立一个“全局注册表”。
        registry = self._build_registry(group, errors)
        registry_ids: Set[str] = set(registry.keys())
        tiers = group.tiers

        if not tiers:
            errors.append("ai.chat.tiers 未配置")
        else:
            self._validate_tier_ref(group.default_tier, "default-tier", tiers, errors)
            self._validate_tier_ref(group.deep_thinking_tier, "deep-thinking-tier", tiers, errors)
            self._validate_tier_candidates(tiers, registry_ids, errors)
            self._validate_tier_enum_coverage(tiers, errors)
            self._validate_deep_thinking_candidates(group, tiers, registry, errors)

        if errors:
            raise ValueError(
                "chat 档位配置校验失败:\n - " + "\n - ".join(errors)
            )

        self._warn_deep_thinking_support(group, tiers, registry)
        logger.info("chat 档位配置校验通过: tiers=%s", list(tiers.keys()) if tiers else [])

    # ==================== 校验器 ====================

    @staticmethod
    def _build_registry(
        group: ModelGroup,
        errors: List[str],
    ) -> Dict[str, ModelCandidate]:
        """
        构建候选注册表（id → 候选），并校验 id 唯一（对应 Java 的 buildRegistry）。
        遍历 candidates 列表，以 id 为 key 构建 Map，如果出现重复 id → 加入错误列表
        空候选跳过；id 缺省时回退 "provider::model" 复合键（与 selector 一致）。
        """
        registry: Dict[str, ModelCandidate] = {}
        candidates = group.candidates or []
        for candidate in candidates:
            if candidate is None:
                continue
            cid = ChatTierConfigValidator._resolve_id(candidate)
            if cid in registry:
                errors.append(f"chat candidates 存在重复 id: {cid}")
            else:
                registry[cid] = candidate
        return registry

    @staticmethod
    #校验 Tier 是否存在
    def _validate_tier_ref(
        tier_name: Optional[str],
        label: str,
        tiers: Dict[str, TierConfig],
        errors: List[str],
    ) -> None:
        """校验默认/深度思考档位引用（对应 Java 的 validateTierRef）。"""
        if not tier_name or not tier_name.strip():
            errors.append(f"{label} 未配置")
        elif tier_name not in tiers:
            errors.append(f"{label} 引用了不存在的档位: {tier_name}")

    @staticmethod
    #校验每个档位的候选列表和超时
    def _validate_tier_candidates(
        tiers: Dict[str, TierConfig],
        registry_ids: Set[str],
        errors: List[str],
    ) -> None:
        """校验每个档位的 timeout_ms 与候选引用（对应 Java 的 validateTierCandidates）。"""
        for tier_name, tier in tiers.items():
            timeout_ms = tier.timeout_ms if tier is not None else None
            if timeout_ms is None:
                errors.append(
                    f"档位 {tier_name} 未配置 timeout-ms"
                    "（必填：流式=首包 TTFT 预算，同步=整段调用上限）"
                )
            elif timeout_ms <= 0:
                errors.append(f"档位 {tier_name} 的 timeout-ms 必须为正数: {timeout_ms}")

            candidates = tier.candidates if tier is not None else None
            if not candidates:
                errors.append(f"档位 {tier_name} 的候选列表为空")
                continue
            for cid in candidates:
                if cid not in registry_ids:
                    errors.append(f"档位 {tier_name} 引用了未在 candidates 注册表登记的 id: {cid}")

    @staticmethod
    def _validate_tier_enum_coverage(
        tiers: Dict[str, TierConfig],
        errors: List[str],
    ) -> None:
        """
        校验 Tier 枚举覆盖（对应 Java 的 validateTierEnumCoverage）。

        Tier 枚举被业务代码直接引用（如 Tier.FAST），每个枚举键都必须有对应档位，
        否则调用点传入该档位覆盖时会在运行期落到"档位缺失"静默降级。
        """
        for tier in Tier:
            if tier.key not in tiers:
                errors.append(f"Tier 枚举 {tier.name} 对应的档位未在 ai.chat.tiers 配置: {tier.key}")

    @staticmethod
    def _validate_deep_thinking_candidates(
        group: ModelGroup,
        tiers: Dict[str, TierConfig],
        registry: Dict[str, ModelCandidate],
        errors: List[str],
    ) -> None:
        """
        校验深度思考档至少有一个可用且支持思考的候选（对应 Java 的 validateDeepThinkingCandidates）。

        否则思考请求经 supportsThinking 过滤后拿到空候选列表、运行期直接失败（硬校验）。
        """
        deep_tier_name = group.deep_thinking_tier
        if not deep_tier_name or not deep_tier_name.strip():
            return
        deep = tiers.get(deep_tier_name) if tiers else None
        if deep is None or not deep.candidates:
            return

        has_thinking_candidate = any(
            ChatTierConfigValidator._is_thinking_candidate(registry.get(cid))
            for cid in deep.candidates
        )
        if not has_thinking_candidate:
            errors.append(f"deep-thinking-tier {deep_tier_name} 无任何已启用且支持思考的候选")

    @staticmethod
    def _warn_deep_thinking_support(
        group: ModelGroup,
        tiers: Dict[str, TierConfig],
        registry: Dict[str, ModelCandidate],
    ) -> None:
        """
        软校验：deep-thinking-tier 候选未声明 supports_thinking 仅逐个告警，不阻止启动
        （对应 Java 的 warnDeepThinkingSupport）。
        """
        if not tiers:
            return
        deep_tier_name = group.deep_thinking_tier
        if not deep_tier_name or not deep_tier_name.strip():
            return
        deep = tiers.get(deep_tier_name)
        if deep is None or not deep.candidates:
            return
        for cid in deep.candidates:
            candidate = registry.get(cid)
            if candidate is not None and candidate.supports_thinking is not True:
                logger.warning(
                    "deep-thinking-tier 候选未声明 supports-thinking，思考请求下将被过滤: id=%s",
                    cid,
                )

    # ==================== 工具 ====================

    @staticmethod
    def _is_thinking_candidate(candidate: Optional[ModelCandidate]) -> bool:
        """候选是否「已登记 & 启用 & 支持思考」。"""
        return (
            candidate is not None
            and candidate.enabled is not False
            and candidate.supports_thinking is True
        )

    @staticmethod
    def _resolve_id(candidate: ModelCandidate) -> str:
        """
        与 ModelSelector 一致的 id 解析：显式 id 优先，缺省回退 "provider::model"。
        """
        if candidate.id and candidate.id.strip():
            return candidate.id
        provider = candidate.provider if candidate.provider else "unknown"
        model = candidate.model if candidate.model else "unknown"
        return f"{provider}::{model}"


def validate_chat_tier_config(properties: AIModelConfig) -> None:
    """
    便捷入口：校验 chat 档位配置（对应 Java 的 afterPropertiesSet 语义）。

    Args:
        properties: AIModelConfig 对象。

    Raises:
        ValueError: 配置非法时抛出。
    """
    ChatTierConfigValidator().validate(properties)
