"""
LLM 歧义确认器（对应 ragent AmbiguityLLMChecker）

仅在规则层无法明确判断时调用，通过 LLM 语义理解确认是否存在品类歧义。

行为对齐 Java：
    - 候选品类文本 → 渲染 guidance-ambiguity-check.st（user 消息）→ FAST 档 LLM 调用；
    - 解析 {ambiguous, reason}；ambiguous 缺失按 True 处理；
    - 非 JSON 对象 / LLM 调用异常 / JSON 解析失败 一律降级 True（触发澄清），不抛错。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.guidance.AmbiguityLLMChecker
    - com.nageoffer.ai.ragent.rag.constant.RAGConstant.GUIDANCE_AMBIGUITY_CHECK_PROMPT_PATH
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from core.llm.chat import LLMService
from core.llm.enums import Tier
from core.llm.schema import ChatRequest, Message
from rag.intent.model import NodeScore
from rag.prompt.formatter import PromptTemplateLoader

logger = logging.getLogger(__name__)

# 歧义确认模板路径（对应 Java RAGConstant.GUIDANCE_AMBIGUITY_CHECK_PROMPT_PATH）
GUIDANCE_AMBIGUITY_CHECK_PROMPT_PATH = "prompt/guidance-ambiguity-check.st"

# 模型偶发包裹 Markdown 代码围栏时的剥离（对应 Java LLMResponseCleaner.stripMarkdownCodeFence，
# 后者整体延后上线，此处只做歧义确认链路需要的最小清理）
_CODE_FENCE = re.compile(r"```(?:json)?\s*|\s*```", re.IGNORECASE)


class AmbiguityLLMChecker:
    """
    LLM 歧义确认器（对应 Java AmbiguityLLMChecker）

    Args:
        llm_service:     LLM 服务（async chat）
        template_loader: 模板加载器，默认 PromptTemplateLoader()
    """

    def __init__(
        self,
        llm_service: LLMService,
        template_loader: Optional[PromptTemplateLoader] = None,
    ):
        self._llm = llm_service
        self._template_loader = template_loader or PromptTemplateLoader()

    async def check_ambiguity(self, question: str, ranked: List[NodeScore]) -> bool:
        """
        调用 LLM 确认是否存在歧义（对应 Java checkAmbiguity）

        Returns:
            bool: True=歧义（触发澄清）；False=不歧义。
            任何异常 / 非 JSON / 缺 ambiguous 字段均降级 True，保证歧义检测永不抛错。
        """
        candidates_text = self._build_candidates_text(ranked)
        prompt = self._template_loader.render(
            GUIDANCE_AMBIGUITY_CHECK_PROMPT_PATH,
            {"question": question, "candidates": candidates_text},
        )
        request = ChatRequest(
            messages=[Message.user(prompt)],
            temperature=0.1,
            topP=0.3,
            thinking=False,
        )

        try:
            raw = await self._llm.chat(request, tier=Tier.FAST)
        except Exception:
            logger.warning("歧义确认 LLM 调用失败, 降级为触发澄清, question=%s", question, exc_info=True)
            return True

        cleaned = _CODE_FENCE.sub("", raw or "").strip()
        try:
            obj = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            logger.warning("歧义确认 LLM 返回非 JSON, 降级为触发澄清: %s", (raw or "")[:200])
            return True

        if not isinstance(obj, dict):
            logger.warning("歧义确认 LLM 返回非 JSON 对象, 降级为触发澄清: %s", (raw or "")[:200])
            return True

        if "ambiguous" not in obj:
            logger.warning("歧义确认 LLM 返回缺少 ambiguous 字段, 降级为触发澄清: %s", (raw or "")[:200])
            return True

        ambiguous = obj["ambiguous"]
        reason = obj.get("reason", "") if isinstance(obj.get("reason"), str) else ""
        logger.info("LLM 歧义确认结果: ambiguous=%s, reason=%s, question=%s", ambiguous, reason, question)
        return bool(ambiguous)

    @staticmethod
    def _build_candidates_text(ranked: List[NodeScore]) -> str:
        """候选品类文本（对应 Java buildCandidatesText）"""
        lines = []
        for ns in ranked or []:
            node = ns.node
            if node is None:
                continue
            system_path = node.full_path if node.full_path else (node.name or "")
            lines.append(
                "- 品类ID: %s, 名称: %s, 路径: %s, 分数: %.2f"
                % (node.id, node.name, system_path, ns.score)
            )
        return "\n".join(lines)
