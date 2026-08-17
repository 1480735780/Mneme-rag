"""
引导式问答服务（对应 ragent IntentGuidanceService）

歧义检测规则链（对齐 Java detectAmbiguity → findAmbiguityGroup 逐行）：
    1. enabled=False → none()（短路，不澄清）；
    2. sub_intents 非空且恰 1 个，且其 KB 候选（>= INTENT_MIN_SCORE）≥ 2；
    3. 按「系统节点 ID」（resolveSystemNodeId）聚合各候选，同系统取最高分者 → ranked 降序 ≥ 2；
    4. shouldSkipGuidance 快速通道：top≤0 / 次分比值 < threshold-margin / 问题含某候选 DOMAIN 级系统名 → 跳过；
    5. confirmAmbiguity 确认：比值 ≥ threshold → 明确歧义；在 [threshold-margin, threshold) → 调 LLM 确认；
    6. trim 到 maxOptions 后渲染 guidance-prompt.st → prompt(decision)。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.guidance.IntentGuidanceService
    - com.nageoffer.ai.ragent.rag.constant.RAGConstant.GUIDANCE_PROMPT_PATH
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from rag.guidance.checker import AmbiguityLLMChecker
from rag.guidance.config import GuidanceProperties
from rag.guidance.decision import GuidanceDecision
from rag.intent import (
    INTENT_MIN_SCORE,
    IntentLevel,
    IntentNode,
    IntentNodeRegistry,
    NodeScore,
    NodeScoreFilters,
    SubQuestionIntent,
)
from rag.prompt.formatter import PromptTemplateLoader

logger = logging.getLogger(__name__)

# 引导提示词模板路径（对应 Java RAGConstant.GUIDANCE_PROMPT_PATH）
GUIDANCE_PROMPT_PATH = "prompt/guidance-prompt.st"

# 归一化：去空白与标点符号（对应 Java \p{Punct}\s；Python 用「非字母数字下划线」等价覆盖，
# 中文/字母数字保留，逗号句号等标点与空白一并去除）
_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)


@dataclass
class AmbiguityGroup:
    """歧义分组（对应 Java 私有 record AmbiguityGroup）"""

    topic_name: str
    ranked: List[NodeScore]


class IntentGuidanceService:
    """
    引导式问答服务（对应 Java IntentGuidanceService）

    Args:
        guidance_properties:   引导配置
        intent_node_registry:  意图节点注册表（按 parentId 上溯用；MVP 注入 DefaultIntentClassifier）
        ambiguity_checker:     LLM 歧义确认器
        template_loader:       模板加载器，默认 PromptTemplateLoader()
    """

    def __init__(
        self,
        guidance_properties: GuidanceProperties,
        intent_node_registry: IntentNodeRegistry,
        ambiguity_checker: AmbiguityLLMChecker,
        template_loader: Optional[PromptTemplateLoader] = None,
    ):
        self._properties = guidance_properties
        self._registry = intent_node_registry
        self._checker = ambiguity_checker
        self._template_loader = template_loader or PromptTemplateLoader()

    async def detect_ambiguity(
        self, question: str, sub_intents: List[SubQuestionIntent]
    ) -> GuidanceDecision:
        """检测歧义并产出决策（对应 Java detectAmbiguity）"""
        if not self._properties.enabled:
            return GuidanceDecision.none()

        group = await self._find_ambiguity_group(question, sub_intents)
        if group is None or not group.ranked:
            return GuidanceDecision.none()

        prompt = self._build_prompt(group.topic_name, group.ranked)
        return GuidanceDecision.of_prompt(prompt)

    # ==================== 歧义分组 ====================

    async def _find_ambiguity_group(
        self, question: str, sub_intents: List[SubQuestionIntent]
    ) -> Optional[AmbiguityGroup]:
        if not sub_intents or len(sub_intents) != 1:
            return None

        candidates = self._filter_candidates(sub_intents[0].node_scores)
        if len(candidates) < 2:
            return None

        # 按系统节点 ID 聚合：同系统取最高分候选
        system_best: dict[str, NodeScore] = {}
        for ns in candidates:
            system_id = self._resolve_system_node_id(ns.node)
            if not system_id:
                continue
            existing = system_best.get(system_id)
            if existing is None or ns.score >= existing.score:
                system_best[system_id] = ns

        ranked = sorted(system_best.values(), key=lambda ns: ns.score, reverse=True)
        if len(ranked) < 2:
            return None

        if self._should_skip_guidance(question, ranked):
            return None

        if not await self._confirm_ambiguity(question, ranked):
            return None

        trimmed = self._trim_ranked_options(ranked)
        topic_name = trimmed[0].node.name if trimmed[0].node else ""
        return AmbiguityGroup(topic_name=topic_name, ranked=trimmed)

    # ==================== 快速通道跳过 ====================

    def _should_skip_guidance(self, question: str, ranked: List[NodeScore]) -> bool:
        top = ranked[0].score
        if top <= 0:
            return True

        # 快速通道 1：次分比值低于边界下限，意图明确
        ratio = ranked[1].score / top
        threshold = self._properties.ambiguity_score_ratio
        margin = self._properties.ambiguity_margin
        if ratio < threshold - margin:
            logger.debug("分数比值(ratio=%.3f)低于边界下限(%.3f), 跳过澄清", ratio, threshold - margin)
            return True

        # 快速通道 2：用户问题显式提到某候选的 DOMAIN 级系统名
        if question and question.strip():
            domain_names = [n for n in (self._resolve_domain_name(ns.node) for ns in ranked) if n]
            normalized_question = self._normalize_name(question)
            for name in dict.fromkeys(domain_names):
                for alias in self._build_system_aliases(name):
                    if len(alias) >= 2 and normalized_question and alias in normalized_question:
                        logger.debug("用户问题包含系统名[%s], 跳过澄清", name)
                        return True
        return False

    async def _confirm_ambiguity(self, question: str, ranked: List[NodeScore]) -> bool:
        top = ranked[0].score
        second = ranked[1].score
        if top <= 0:
            return False

        ratio = second / top
        threshold = self._properties.ambiguity_score_ratio
        margin = self._properties.ambiguity_margin

        if ratio >= threshold:
            logger.info("分数比值(ratio=%.3f)超过阈值(%.3f), 判定为歧义", ratio, threshold)
            return True

        if ratio >= threshold - margin:
            logger.info("分数比值(ratio=%.3f)在边界区间[%.3f, %.3f), 调 LLM 确认", ratio, threshold - margin, threshold)
            return await self._checker.check_ambiguity(question, ranked)

        # ratio < threshold - margin（且未被快速通道拦下），不触发澄清
        return False

    # ==================== 候选与树操作 ====================

    @staticmethod
    def _filter_candidates(scores: List[NodeScore]) -> List[NodeScore]:
        """过滤 KB 意图且分数 >= INTENT_MIN_SCORE（对应 Java filterCandidates）"""
        return NodeScoreFilters.kb_with_min_score(scores or [], INTENT_MIN_SCORE)

    def _resolve_domain_name(self, node: Optional[IntentNode]) -> str:
        """上溯取 DOMAIN 级名称（对应 Java resolveDomainName）"""
        current = node
        while current is not None:
            if current.level == IntentLevel.DOMAIN:
                return current.name or ""
            current = self._fetch_parent(current)
        return ""

    def _build_system_aliases(self, system_name: str) -> List[str]:
        """系统名别名（对应 Java buildSystemAliases，当前仅归一化后的原名）"""
        if not system_name or not system_name.strip():
            return []
        normalized = self._normalize_name(system_name)
        return [normalized] if normalized else []

    def _resolve_system_node_id(self, node: Optional[IntentNode]) -> str:
        """上溯取系统节点 ID（对应 Java resolveSystemNodeId）"""
        if node is None:
            return ""
        current = node
        parent = self._fetch_parent(current)
        while True:
            level = current.level
            if level == IntentLevel.CATEGORY and (parent is None or parent.level == IntentLevel.DOMAIN):
                return current.id
            if parent is None:
                return current.id
            current = parent
            parent = self._fetch_parent(current)

    def _fetch_parent(self, node: Optional[IntentNode]) -> Optional[IntentNode]:
        """取父节点（对应 Java fetchParent）"""
        if node is None or not node.parent_id or not node.parent_id.strip():
            return None
        return self._registry.get_node_by_id(node.parent_id)

    def _trim_ranked_options(self, ranked: List[NodeScore]) -> List[NodeScore]:
        """截断到 maxOptions（对应 Java trimRankedOptions）"""
        max_options = self._properties.max_options
        if max_options is None or len(ranked) <= max_options:
            return ranked
        return ranked[:max_options]

    # ==================== 澄清文案 ====================

    def _build_prompt(self, topic_name: str, ranked: List[NodeScore]) -> str:
        options = self._render_options(ranked)
        return self._template_loader.render(
            GUIDANCE_PROMPT_PATH,
            {"topic_name": topic_name or "", "options": options},
        )

    @staticmethod
    def _render_options(ranked: List[NodeScore]) -> str:
        """逐项编号渲染候选（对应 Java renderOptions）"""
        lines = []
        for i, ns in enumerate(ranked, start=1):
            display = IntentGuidanceService._resolve_option_display(ns.node)
            lines.append(f"{i}) {display}")
        return "\n".join(lines).strip()

    @staticmethod
    def _resolve_option_display(node: Optional[IntentNode]) -> str:
        """候选展示文案：fullPath 优先，其次 name，最后 id（对应 Java resolveOptionDisplay）"""
        if node is None:
            return ""
        if node.full_path and node.full_path.strip():
            return node.full_path
        if node.name and node.name.strip():
            return node.name
        return node.id

    @staticmethod
    def _normalize_name(name: str) -> str:
        """归一化名称：去空白与标点、转小写（对应 Java normalizeName）"""
        if name is None:
            return ""
        return _NON_ALNUM.sub("", name.strip().lower())
