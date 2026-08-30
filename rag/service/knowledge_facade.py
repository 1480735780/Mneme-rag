# -*- coding: utf-8 -*-
"""
知识检索门面：Agent 模式下 rag 对外的唯一检索窄口（对应 Java KnowledgeSearchFacade）

管线：改写 → 意图解析（内置 KB-only 过滤）→ 歧义引导 → 多通道检索 → KB_ANSWER 合成，
返回可直接引用的答案文本。引用/来源装配定死不走，与引用开关无关。
近期轮次只喂给改写做指代消解，合成阶段不带历史：工具结论只依据本次证据。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.service.KnowledgeSearchFacade
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.llm.chat import LLMService
from core.llm.schema import ChatRequest, Message, RetrievedChunk
from rag.guidance.service import IntentGuidanceService
from rag.intent import IntentResolver, NodeScore, NodeScoreFilters, SubQuestionIntent
from rag.prompt.builder import PromptContext, RAGPromptService
from rag.prompt.formatter import ContextFormatter
from rag.retrieval.channel.scope_resolver import RetrievalScopeResolver
from rag.retrieval.engine import MultiChannelRetrievalEngine
from rag.retrieval.schema import MULTI_CHANNEL_KEY, RetrievalBudget, RetrievalContext
from rag.rewrite import QueryRewriteService
from rag.source import CitationContextEnricher

logger = logging.getLogger(__name__)

# 空检索兜底文案（对应 Java KnowledgeSearchFacade.EMPTY_RESULT）
EMPTY_RESULT = "未在知识库中检索到与该问题相关的内容。"


class KnowledgeSearchFacade:
    """
    知识检索门面：主 Agent 的 search_knowledge 工具经此调用完整 RAG 管线

    Args:
        query_rewrite_service: 查询改写服务（rewrite_with_split；历史仅参与指代消解）
        intent_resolver:       意图解析器（resolve + merge_intent_group）
        guidance_service:      歧义引导服务（detect_ambiguity）
        retrieval_engine:      多通道检索引擎（retrieve_knowledge_channels）
        budget:                检索预算（召回扇出 / 上下文条数）
        scope_resolver:        检索作用域解析器（零意图子问题在此回落全局检索）
        context_formatter:     KB 上下文格式化器（按意图归属组装证据）
        citation_enricher:     引用上下文注入器（仅用其抹除内部 docId 锚点）
        prompt_service:        RAG Prompt 编排服务（build_structured_messages）
        llm_service:           LLM 服务（同步 chat 合成最终答案）
    """

    def __init__(
        self,
        query_rewrite_service: QueryRewriteService,
        intent_resolver: IntentResolver,
        guidance_service: IntentGuidanceService,
        retrieval_engine: MultiChannelRetrievalEngine,
        budget: RetrievalBudget,
        scope_resolver: Optional[RetrievalScopeResolver],
        context_formatter: ContextFormatter,
        citation_enricher: CitationContextEnricher,
        prompt_service: RAGPromptService,
        llm_service: LLMService,
    ) -> None:
        self._query_rewrite_service = query_rewrite_service
        self._intent_resolver = intent_resolver
        self._guidance_service = guidance_service
        self._retrieval_engine = retrieval_engine
        self._budget = budget
        self._scope_resolver = scope_resolver
        self._context_formatter = context_formatter
        self._citation_enricher = citation_enricher
        self._prompt_service = prompt_service
        self._llm_service = llm_service

    async def search(self, query: str, recent_history: Optional[List[Message]] = None) -> str:
        """
        检索并合成答案，供主 Agent 的 search_knowledge 工具调用

        Args:
            query:         完整、独立、可单独读懂的疑问句（工具参数 query）
            recent_history: 主 Agent 会话的近期 user/assistant 轮次，仅用于改写阶段的指代消解
        """
        rewrite_result = await self._query_rewrite_service.rewrite_with_split(query, recent_history)
        sub_intents = self._filter_kb_only(await self._intent_resolver.resolve(rewrite_result))

        decision = await self._guidance_service.detect_ambiguity(
            rewrite_result.rewritten_question, sub_intents
        )
        if decision.is_prompt():
            logger.info(
                "Agent 知识库检索命中歧义引导，跳过检索与答案合成, question=%s",
                rewrite_result.rewritten_question,
            )
            return decision.prompt

        retrieval_ctx = await self._retrieve_kb(sub_intents)
        if not retrieval_ctx.has_kb():
            return EMPTY_RESULT

        # 工具不渲染角标，但内部 docId 一定要抹掉，否则会随工具结果漏进主 Agent 的可见文本
        kb_context = self._citation_enricher.strip_doc_id_anchors(retrieval_ctx.kb_context)

        merged_group = self._intent_resolver.merge_intent_group(sub_intents)
        prompt_context = PromptContext(
            question=rewrite_result.rewritten_question,
            kb_context=kb_context,
            kb_intents=merged_group.kb_intents,
            retrieved_intent_ids=retrieval_ctx.get_retrieved_intent_ids(),
        )
        messages = self._prompt_service.build_structured_messages(
            prompt_context,
            None,
            rewrite_result.rewritten_question,
            list(rewrite_result.sub_questions),
        )

        return await self._llm_service.chat(
            ChatRequest(messages=messages, temperature=0.0, topP=1.0, thinking=False)
        )

    # ==================== 内部辅助 ====================

    @staticmethod
    def _filter_kb_only(sub_intents: List[SubQuestionIntent]) -> List[SubQuestionIntent]:
        """
        只保留 KB 意图：MCP 走原生工具，SYSTEM 由主 Agent 人设直接承担

        剩零意图的子问题照样往下走，不在此拦截：作用域判定只有 RetrievalScopeResolver
        一份，零意图在那里回落全局检索。拦在这里等于把工具调用否决两次——
        主 Agent 已判过一次「该查知识库」。
        """
        return [
            SubQuestionIntent(
                sub_question=si.sub_question,
                node_scores=NodeScoreFilters.kb(si.node_scores),
            )
            for si in sub_intents
        ]

    async def _retrieve_kb(self, sub_intents: List[SubQuestionIntent]) -> RetrievalContext:
        """
        KB-only 检索（对应 Java RetrievalEngine.retrieve 的 KB 分支；无 KB 意图不触发 MCP 分支）

        每个子问题一次 retrieve_knowledge_channels：作用域由 RetrievalScopeResolver 解析、
        多通道并行召回 → 后处理 → 归因；单子问题失败降级为空、不影响其余子问题。
        """
        merged_intent_chunks: Dict[str, List[RetrievedChunk]] = {}
        for si in sub_intents:
            try:
                result = await self._retrieval_engine.retrieve_knowledge_channels(
                    si, self._budget, self._scope_resolver
                )
            except Exception:  # noqa: BLE001 单子问题检索失败降级为空，不影响其余子问题
                logger.error("子问题检索失败，降级为空，question：%s", si.sub_question, exc_info=True)
                continue
            if result.chunks:
                for intent_id, chunks in result.group_by_intent(MULTI_CHANNEL_KEY).items():
                    if chunks:
                        merged_intent_chunks.setdefault(intent_id, []).extend(chunks)

        all_chunks = [c for chunks in merged_intent_chunks.values() for c in chunks]
        kb_context = ""
        if all_chunks:
            # 借 RetrievalContext 的既有语义取「有文档归属的意图 ID」（排除无归属全局键）
            attribution = RetrievalContext(intent_chunks=merged_intent_chunks)
            kb_context = self._context_formatter.format_kb_context(
                self._merged_kb_intents(sub_intents),
                attribution.get_retrieved_intent_ids(),
                all_chunks,
                self._budget.context_top_k,
            )

        return RetrievalContext(
            kb_context=kb_context or None,
            intent_chunks=merged_intent_chunks,
        )

    @staticmethod
    def _merged_kb_intents(sub_intents: List[SubQuestionIntent]) -> List[NodeScore]:
        """跨子问题聚合 KB 意图：按意图 ID 去重保序（与 RAGChatEngine._merged_kb_intents 同语义）"""
        seen: List[NodeScore] = []
        seen_ids: set = set()
        for si in sub_intents or []:
            for ns in NodeScoreFilters.kb(si.node_scores):
                if ns.node.id not in seen_ids:
                    seen_ids.add(ns.node.id)
                    seen.append(ns)
        return seen
