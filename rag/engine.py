# -*- coding: utf-8 -*-
"""
RAG 对话引擎（对应 Java RAGChatServiceImpl + StreamChatPipeline）

主编排管线（对齐 Java StreamChatPipeline.execute）：
    记忆加载 → 查询改写/拆分 → 意图解析 → 歧义引导（短路）→ 纯系统意图（短路）→
    检索（按 KB 意图定向召回 + 上下文格式化）→ 空结果兜底（短路）→ 来源/引用/grounding 装配 → Prompt 组装 → LLM 流式输出

短路分支（handleXxx 返回 True 即已处理并停止后续阶段，对齐 Java 的 boolean 返回约定）：
    - handle_guidance：歧义时直接把澄清文案当回答推给用户，不再检索；
    - handle_system_only：全部子问题均为纯系统意图，直接用系统提示词回答，不走检索；
    - handle_empty_retrieval：检索无命中，推送固定兜底文案。

MVP 差异（相对 Java）：
    - 记忆：Java 走 ConversationMemoryService（Redis/DB，C 层）；Python 定义接口 +
      进程内 NoopConversationMemoryService 默认实现（load 空历史、append 不落库），
      真实后端实现接口注入即可替换，语义一致（onReplyToMessageId 在无消息 ID 时跳过）。
    - 检索执行：Java RetrievalEngine 按子问题跑通道并依据 chunk→intent 归属回填
      intentChunks；Python 复用 MultiChannelRetrievalEngine（单次 SearchContext 全链路：
      并行通道 → 去重 → RRF 融合 → Rerank），按「每个 KB 意图一次定向召回」得到
      意图归属；无 KB 意图时做一次全局召回，片段挂到 MULTI_CHANNEL_KEY 下。
      作用域收窄（RetrievalScopeResolver）与 MCP 工具编排属 B/C 层，MVP 不实现。
    - 任务取消：Java 用 taskManager.bindHandle 绑定 StreamCancellationHandle；
      Python 无等价句柄（stream_chat 返回 None），由调用方直接取消 execute 协程，故省略。
    - 链路追踪 / 日志脱敏：@RagTraceNode 与 LogSafe 延后上线，Python 用 logging 简要记录。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.RAGChatServiceImpl
    - com.nageoffer.ai.ragent.rag.service.pipeline.StreamChatPipeline
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from core.llm.callback import StreamCallback
from core.llm.chat import LLMService
from core.llm.schema import ChatRequest, Message, RetrievedChunk
from rag.guidance.decision import GuidanceDecision
from rag.guidance.service import IntentGuidanceService
from rag.intent import (
    IntentGroup,
    IntentNode,
    IntentResolver,
    NodeScore,
    NodeScoreFilters,
    SubQuestionIntent,
)
from rag.prompt.builder import (
    AgentPromptResolver,
    AgentPromptSlot,
    PromptContext,
    RAGPromptService,
    StaticAgentPromptResolver,
)
from rag.prompt.formatter import ContextFormatter, DefaultContextFormatter
from rag.retrieval.engine import MultiChannelRetrievalEngine
from rag.retrieval.schema import (
    MULTI_CHANNEL_KEY,
    RetrievalBudget,
    RetrievalContext,
    RetrievalScope,
    SearchContext,
)
from rag.rewrite import QueryRewriteService, RewriteResult
from rag.source import (
    CitationContextEnricher,
    GroundingChunksAssembler,
    SourcesAssembler,
)

logger = logging.getLogger(__name__)

# 空检索兜底文案（对应 Java StreamChatPipeline.handleEmptyRetrieval）
EMPTY_RETRIEVAL_MESSAGE = "未检索到与问题相关的文档内容。"


class ConversationMemoryService(ABC):
    """
    会话记忆服务接口（对应 Java ConversationMemoryService）

    加载历史消息供改写与 Prompt 组装消费，同时记录用户消息为后续落库预留位置。
    C 层将提供 Redis/DB 实现；A 层 MVP 以 NoopConversationMemoryService 兜底。
    """

    @abstractmethod
    def load(self, conversation_id: Optional[str], user_id: Optional[str]) -> List[Message]:
        """
        加载会话历史（对应 Java load）

        Returns:
            List[Message]: 历史消息列表（含摘要时以 system 消息作为首条），无历史返回空列表
        """
        ...

    @abstractmethod
    def append(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> Optional[str]:
        """
        记录一条消息（对应 Java append）

        Returns:
            Optional[str]: 新消息 ID（用于 onReplyToMessageId 关联回答）；不落库返回 None
        """
        ...


class NoopConversationMemoryService(ConversationMemoryService):
    """空实现：不加载、不落库（MVP 兜底 / 测试注入）"""

    def load(self, conversation_id: Optional[str], user_id: Optional[str]) -> List[Message]:
        return []

    def append(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> Optional[str]:
        return None


@dataclass
class StreamChatContext:
    """
    流式对话上下文（对应 Java StreamChatContext）

    一次 execute 调用全阶段共享的载体：入参（问题/回调/会话标识）与
    各阶段产物（history / rewrite_result / sub_intents）。

    Attributes:
        question:        用户本次问题
        callback:        流式回调（on_content / on_complete / on_sources / ...）
        conversation_id: 会话 ID（记忆服务键，可为 None）
        user_id:         用户 ID（记忆服务键，可为 None）
        task_id:         任务 ID（预留，供取消绑定；MVP 不消费）
        deep_thinking:   深度思考开关（透传最终 ChatRequest.thinking）
        history:         加载到的历史消息（记忆阶段写入）
        rewrite_result:  改写 + 拆分结果（改写阶段写入）
        sub_intents:     子问题意图列表（意图解析阶段写入）
    """

    question: str
    callback: StreamCallback
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    task_id: Optional[str] = None
    deep_thinking: bool = False
    history: List[Message] = field(default_factory=list)
    rewrite_result: Optional[RewriteResult] = None
    sub_intents: List[SubQuestionIntent] = field(default_factory=list)


class RAGChatEngine:
    """
    RAG 对话引擎（对应 Java StreamChatPipeline）

    通过 execute(context) 驱动「提问 → 检索 → Prompt → 生成 → 引用」闭环。
    各阶段均为异步方法 + boolean 短路返回值，与 Java 的 handleXxx 约定一一对应。

    Args:
        query_rewrite_service:   查询改写 + 多问句拆分服务（必需注入）
        intent_resolver:         意图解析器（必需注入）
        guidance_service:        歧义引导服务（必需注入）
        retrieval_engine:        多通道检索引擎（必需注入）
        llm_service:             LLM 流式服务（必需注入）
        prompt_builder:          RAG Prompt 编排服务（必需注入）
        memory_service:          会话记忆服务，默认 NoopConversationMemoryService()
        agent_prompt_resolver:   智能体提示词解析器，默认 StaticAgentPromptResolver()
        sources_assembler:       文档来源装配器，默认 SourcesAssembler()
        grounding_chunks_assembler: 推荐问题 grounding 装配器，默认 GroundingChunksAssembler()
        citation_context_enricher:  引用编号注入器，默认 CitationContextEnricher()
        context_formatter:       KB 上下文格式化器，默认 DefaultContextFormatter()
        retrieval_budget:        检索预算，默认 RetrievalBudget(recall_budget=20, candidate_limit=40, context_top_k=10)
        active_collections:      全部有效知识库 collection（无 KB 意图时的全局召回范围），默认空列表
    """

    def __init__(
        self,
        query_rewrite_service: QueryRewriteService,
        intent_resolver: IntentResolver,
        guidance_service: IntentGuidanceService,
        retrieval_engine: MultiChannelRetrievalEngine,
        llm_service: LLMService,
        prompt_builder: RAGPromptService,
        *,
        memory_service: Optional[ConversationMemoryService] = None,
        agent_prompt_resolver: Optional[AgentPromptResolver] = None,
        sources_assembler: Optional[SourcesAssembler] = None,
        grounding_chunks_assembler: Optional[GroundingChunksAssembler] = None,
        citation_context_enricher: Optional[CitationContextEnricher] = None,
        context_formatter: Optional[ContextFormatter] = None,
        retrieval_budget: Optional[RetrievalBudget] = None,
        active_collections: Optional[List[str]] = None,
    ):
        self._memory_service = memory_service or NoopConversationMemoryService()
        self._query_rewrite_service = query_rewrite_service
        self._intent_resolver = intent_resolver
        self._guidance_service = guidance_service
        self._retrieval_engine = retrieval_engine
        self._llm_service = llm_service
        self._prompt_builder = prompt_builder
        self._agent_prompt_resolver = (
            agent_prompt_resolver or StaticAgentPromptResolver()
        )
        self._sources_assembler = sources_assembler or SourcesAssembler()
        self._grounding_assembler = grounding_chunks_assembler or GroundingChunksAssembler()
        self._citation_enricher = citation_context_enricher or CitationContextEnricher()
        self._context_formatter = context_formatter or DefaultContextFormatter()
        # 漏斗单调：recall_budget >= context_top_k 且 candidate_limit >= context_top_k（对齐 Java 启动校验）
        self._budget = retrieval_budget or RetrievalBudget(
            recall_budget=20, candidate_limit=40, context_top_k=10
        )
        self._active_collections = list(active_collections or [])

    # ==================== 主编排 ====================

    async def execute(self, context: StreamChatContext) -> None:
        """执行流式对话管线（对应 Java StreamChatPipeline.execute）"""
        await self._load_memory(context)
        await self._rewrite_query(context)
        await self._resolve_intents(context)

        if await self._handle_guidance(context):
            return
        if await self._handle_system_only(context):
            return

        retrieval_ctx = await self._retrieve(context)
        if await self._handle_empty_retrieval(context, retrieval_ctx):
            return

        await self._stream_rag_response(context, retrieval_ctx)

    # ==================== 流水线阶段 ====================

    async def _load_memory(self, context: StreamChatContext) -> None:
        """加载历史并登记用户消息（对应 Java loadMemory）"""
        history = self._memory_service.load(context.conversation_id, context.user_id)
        question_message_id = self._memory_service.append(
            context.conversation_id, context.user_id, Message.user(context.question)
        )
        if question_message_id:
            await context.callback.on_reply_to_message_id(question_message_id)
        context.history = history or []

    async def _rewrite_query(self, context: StreamChatContext) -> None:
        """改写 + 拆分（对应 Java rewriteQuery）"""
        context.rewrite_result = await self._query_rewrite_service.rewrite_with_split(
            context.question, context.history
        )

    async def _resolve_intents(self, context: StreamChatContext) -> None:
        """意图解析（对应 Java resolveIntents）"""
        context.sub_intents = await self._intent_resolver.resolve(context.rewrite_result)

    async def _handle_guidance(self, context: StreamChatContext) -> bool:
        """歧义引导：需澄清时直接把文案推给用户并停止后续（对应 Java handleGuidance）"""
        decision: GuidanceDecision = await self._guidance_service.detect_ambiguity(
            context.rewrite_result.rewritten_question, context.sub_intents
        )
        if not decision.is_prompt():
            return False
        await context.callback.on_content(decision.prompt)
        await context.callback.on_complete()
        return True

    async def _handle_system_only(self, context: StreamChatContext) -> bool:
        """纯系统意图：用系统提示词直接回答，不走检索（对应 Java handleSystemOnly）"""
        all_system_only = all(
            self._intent_resolver.is_system_only(si.node_scores)
            for si in context.sub_intents
        )
        if not all_system_only:
            return False

        custom_prompt = next(
            (
                ns.node.prompt_template
                for si in context.sub_intents
                for ns in si.node_scores
                if ns.node is not None
                and ns.node.prompt_template
                and ns.node.prompt_template.strip()
            ),
            None,
        )
        await self._stream_system_response(
            context.rewrite_result.rewritten_question,
            context.history,
            custom_prompt,
            context.callback,
        )
        return True

    async def _retrieve(self, context: StreamChatContext) -> RetrievalContext:
        """
        检索：按 KB 意图定向召回并格式化上下文（对应 Java RetrievalEngine.retrieve）

        - 有 KB 意图：每个意图一次定向召回（作用域指向该意图的 collection），
          命中片段按意图 ID 分组；召回在引擎层已含 去重 → 融合 → Rerank 全链路。
        - 无 KB 意图：做一次全局召回，片段挂到 MULTI_CHANNEL_KEY 下（无归属）。
        最后把各意图片段合并后交给上下文格式化器（其内部按 context_top_k 截断）。
        """
        sub_intents = context.sub_intents or []
        if not sub_intents:
            return RetrievalContext(intent_chunks={})

        kb_intents = self._merged_kb_intents(sub_intents)
        intent_chunks: Dict[str, List[RetrievedChunk]] = {}
        all_chunks: List[RetrievedChunk] = []

        if kb_intents:
            for node_score in kb_intents:
                node: IntentNode = node_score.node
                collections = node.get_effective_collection_names()
                if not collections:
                    logger.debug("KB 意图 %s 未配置 collection，跳过定向召回", node.id)
                    continue
                try:
                    chunks = await self._retrieval_engine.retrieve(
                        self._build_search_context(context, node_score, collections)
                    )
                except Exception:  # noqa: BLE001 单意图召回失败降级，不影响其余意图
                    logger.error("意图 %s 定向召回失败，降级为空", node.id, exc_info=True)
                    chunks = []
                if chunks:
                    intent_chunks[node.id] = chunks
                    all_chunks.extend(chunks)
        else:
            try:
                chunks = await self._retrieval_engine.retrieve(
                    self._build_search_context(
                        context, None, self._active_collections, directed=False
                    )
                )
            except Exception:  # noqa: BLE001 全局召回失败按空结果兜底
                logger.error("无 KB 意图全局召回失败，降级为空", exc_info=True)
                chunks = []
            if chunks:
                intent_chunks[MULTI_CHANNEL_KEY] = chunks
                all_chunks = chunks

        kb_context = ""
        if all_chunks:
            kb_context = self._context_formatter.format_kb_context(
                kb_intents,
                self._retrieved_intent_ids(intent_chunks),
                all_chunks,
                self._budget.context_top_k,
            )

        return RetrievalContext(kb_context=kb_context, intent_chunks=intent_chunks)

    async def _handle_empty_retrieval(self, context: StreamChatContext, retrieval_ctx: RetrievalContext) -> bool:
        """空检索兜底：推送固定文案并停止后续（对应 Java handleEmptyRetrieval）"""
        if not retrieval_ctx.is_empty():
            return False
        await context.callback.on_content(EMPTY_RETRIEVAL_MESSAGE)
        await context.callback.on_complete()
        return True

    async def _stream_rag_response(self, context: StreamChatContext, retrieval_ctx: RetrievalContext) -> None:
        """来源/引用/grounding 装配 + Prompt 组装 + LLM 流式输出（对应 Java streamRagResponse）"""
        intent_group = self._intent_resolver.merge_intent_group(context.sub_intents)

        # 检索完成后建立唯一来源编号：同一列表用于完成事件、来源面板与消息落库，
        # 开启引用时还作为行内角标编号（对齐 Java SourcesAssembler.assemble）
        sources = self._sources_assembler.assemble(retrieval_ctx.intent_chunks)
        await context.callback.on_sources(sources)

        # 引用开关关闭时这一步只负责清掉上下文里的内部 docId，不注入编号
        retrieval_ctx.kb_context = self._citation_enricher.enrich(
            retrieval_ctx.kb_context, sources
        )

        # grounding 片段随消息落库，供答案后推荐追问生成（不参与 Prompt）
        await context.callback.on_grounding_chunks(
            self._grounding_assembler.assemble(retrieval_ctx.intent_chunks)
        )

        await self._stream_llm_response(
            context.rewrite_result,
            retrieval_ctx,
            intent_group,
            context.history,
            context.deep_thinking,
            context.callback,
        )

    # ==================== LLM 响应 ====================

    async def _stream_system_response(
        self,
        question: str,
        history: Optional[List[Message]],
        custom_prompt: Optional[str],
        callback: StreamCallback,
    ) -> None:
        """纯系统意图响应：system + 历史 + 问题（对应 Java streamSystemResponse）"""
        system_prompt = (
            custom_prompt
            if custom_prompt and custom_prompt.strip()
            else self._agent_prompt_resolver.resolve(AgentPromptSlot.SYSTEM_CHAT)
        )
        messages: List[Message] = []
        if system_prompt and system_prompt.strip():
            messages.append(Message.system(system_prompt))
        if history:
            messages.extend(history)
        messages.append(Message.user(question))

        request = ChatRequest(messages=messages, temperature=0.7, thinking=False)
        await self._llm_service.stream_chat(request, callback)

    async def _stream_llm_response(
        self,
        rewrite_result: RewriteResult,
        retrieval_ctx: RetrievalContext,
        intent_group: IntentGroup,
        history: Optional[List[Message]],
        deep_thinking: bool,
        callback: StreamCallback,
    ) -> None:
        """RAG 响应：Prompt 组装 + 流式调用（对应 Java streamLLMResponse）"""
        prompt_context = PromptContext(
            question=rewrite_result.rewritten_question,
            mcp_context=retrieval_ctx.mcp_context,
            kb_context=retrieval_ctx.kb_context,
            mcp_intents=intent_group.mcp_intents,
            kb_intents=intent_group.kb_intents,
            retrieved_intent_ids=retrieval_ctx.get_retrieved_intent_ids(),
        )
        messages = self._prompt_builder.build_structured_messages(
            prompt_context,
            history,
            rewrite_result.rewritten_question,
            list(rewrite_result.sub_questions),
        )
        has_mcp = retrieval_ctx.has_mcp()
        request = ChatRequest(
            messages=messages,
            thinking=deep_thinking,
            temperature=0.3 if has_mcp else 0.0,  # MCP 场景稍微放宽温度（对齐 Java）
            topP=0.8 if has_mcp else 1.0,
        )
        await self._llm_service.stream_chat(request, callback)

    # ==================== 私有辅助 ====================

    def _build_search_context(
        self,
        context: StreamChatContext,
        kb_intent: Optional[NodeScore],
        collections: Optional[List[str]],
        directed: bool = True,
    ) -> SearchContext:
        """构造一次多通道检索的 SearchContext（对应 Java buildSubQuestionContext 的检索部分）"""
        if directed and kb_intent is not None:
            scope = RetrievalScope(
                directed=True,
                top_score=kb_intent.score,
                intents=[kb_intent],
                target_collections=list(collections or []),
                supplement_collections=[],
            )
        else:
            scope = RetrievalScope.global_scope(0.0, list(collections or []))

        return SearchContext(
            original_question=context.question,
            rewritten_question=context.rewrite_result.rewritten_question,
            sub_questions=list(context.rewrite_result.sub_questions),
            intents=[kb_intent] if kb_intent is not None else [],
            budget=self._budget,
            retrieval_scope=scope,
        )

    @staticmethod
    def _merged_kb_intents(sub_intents: List[SubQuestionIntent]) -> List[NodeScore]:
        """跨子问题聚合 KB 意图：按意图 ID 去重保序（对应 Java mergedIntentChunks 的去重语义）"""
        seen: List[NodeScore] = []
        seen_ids = set()
        for si in sub_intents or []:
            for ns in NodeScoreFilters.kb(si.node_scores):
                if ns.node.id not in seen_ids:
                    seen_ids.add(ns.node.id)
                    seen.append(ns)
        return seen

    @staticmethod
    def _retrieved_intent_ids(intent_chunks: Dict[str, List[RetrievedChunk]]) -> Set[str]:
        """有文档归属的意图 ID（排除无归属全局键；对应 RetrievalContext.getRetrievedIntentIds）"""
        return {
            intent_id
            for intent_id in intent_chunks
            if intent_id != MULTI_CHANNEL_KEY
        }
