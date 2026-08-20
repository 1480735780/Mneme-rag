# -*- coding: utf-8 -*-
"""
rag.service.recommended_question_service - 推荐追问 service（对应 Java RecommendedQuestionService/Impl +
RecommendedQuestionGenerator + RecommendedQuestionsPayload）

流程（对齐 Java RecommendedQuestionServiceImpl.generate）：
    1. 校验 assistant 消息归属（存在 + 归属用户 + role=assistant）；
    2. 关闭判定：message_status 非空且 != NORMAL → 不推荐（EMPTY）；
    3. 命中缓存（recommended_questions 非 None，含空数组负缓存）→ 直接返回；
    4. 取用户提问（reply_to_message_id → conv/用户/role=user 的那条内容），剥 CitationMarkup 后
       作为答案，grounding chunks 作为证据，调 Generator（LLM FAST 档）生成；
    5. SUCCESS/EMPTY 都写回 recommended_questions（空=有效负缓存），FAILED 不落库。

语义（对齐 Java docstring）：null=未生成，空数组=已生成但无合适追问，非空=生成成功。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.RecommendedQuestionService
    - com.nageoffer.ai.ragent.rag.service.impl.RecommendedQuestionServiceImpl
    - com.nageoffer.ai.ragent.rag.service.impl.RecommendedQuestionGenerator
    - com.nageoffer.ai.ragent.rag.dto.RecommendedQuestionsPayload
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from common.exception.business import ClientException
from core.llm.enums import Tier
from core.llm.schema import ChatRequest, GroundingChunk, Message
from rag.dao.message_dao import MessageDao
from rag.prompt.builder import AgentPromptResolver, AgentPromptSlot, StaticAgentPromptResolver
from rag.source import CitationMarkup

logger = logging.getLogger(__name__)

# 推荐追问生成常量（对齐 Java RecommendedQuestionGenerator）
DEFAULT_RECOMMEND_COUNT = 3
MAX_QUESTION_CHARS = 1000
MAX_ANSWER_CHARS = 6000
MAX_CHUNKS_CHARS = 6000
MAX_OUTPUT_TOKENS = 256
MAX_QUESTION_ITEM_CHARS = 200

# 消息状态（对齐 Java ChatMessage.MessageStatus.name，DB 存大写）
_STATUS_NORMAL = "NORMAL"


class RecommendedQuestionsStatus(Enum):
    """生成结果状态（对齐 Java RecommendedQuestionsPayload.Status）"""

    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


@dataclass
class RecommendedQuestionsPayload:
    """推荐追问生成结果（对应 Java RecommendedQuestionsPayload record）"""

    status: RecommendedQuestionsStatus
    questions: List[str] = field(default_factory=list)

    @classmethod
    def success(cls, questions: Optional[List[str]]) -> "RecommendedQuestionsPayload":
        return cls(
            RecommendedQuestionsStatus.SUCCESS, list(questions)
        ) if questions else cls.empty()

    @classmethod
    def empty(cls) -> "RecommendedQuestionsPayload":
        return cls(RecommendedQuestionsStatus.EMPTY, [])

    @classmethod
    def failed(cls) -> "RecommendedQuestionsPayload":
        return cls(RecommendedQuestionsStatus.FAILED, [])


class RecommendedQuestionGenerator:
    """推荐追问问题生成器（对应 Java RecommendedQuestionGenerator）：LLM FAST 档派生"""

    def __init__(
        self,
        agent_prompt_resolver: Optional[AgentPromptResolver] = None,
        llm_service: Optional[object] = None,
    ):
        self._resolver = agent_prompt_resolver or StaticAgentPromptResolver()
        self._llm_service = llm_service

    async def generate(
        self,
        question: Optional[str],
        answer: Optional[str],
        chunks: Optional[List[GroundingChunk]],
    ) -> RecommendedQuestionsPayload:
        """LLM 生成推荐追问；任何异常 → FAILED（不阻断调用方）"""
        try:
            count = DEFAULT_RECOMMEND_COUNT
            prompt = self._resolver.render(
                AgentPromptSlot.RECOMMENDED_QUESTIONS,
                {
                    "question": _sub_pre(question or "", MAX_QUESTION_CHARS),
                    "answer": _sub_pre(answer or "", MAX_ANSWER_CHARS),
                    "count": str(count),
                },
            )
            prompt = prompt.replace("{chunks}", _build_chunks_text(chunks))
            request = ChatRequest(
                messages=[Message.user(prompt)],
                temperature=0.7,
                topP=0.8,
                maxTokens=MAX_OUTPUT_TOKENS,
                thinking=False,
            )
            raw = await self._llm_service.chat(request, Tier.FAST)
            return self._parse_questions(raw, count)
        except Exception:  # noqa: BLE001 —— 生成失败降级 FAILED，不阻断
            logger.warning("生成推荐追问问题失败", exc_info=True)
            return RecommendedQuestionsPayload.failed()

    # ---------- 解析 ----------

    def _parse_questions(self, raw: Optional[str], count: int) -> RecommendedQuestionsPayload:
        """健壮解析：去代码围栏 -> JSON 数组 -> trim/去空/去重/截断；任何异常或非数组视为无结果"""
        if not raw or not raw.strip():
            return RecommendedQuestionsPayload.failed()
        stripped = _strip_code_fence(raw).strip()
        if not stripped:
            return RecommendedQuestionsPayload.failed()
        try:
            array = json.loads(stripped)
            if not isinstance(array, list):
                return RecommendedQuestionsPayload.failed()
            result: List[str] = []
            seen = set()
            for item in array:
                if not isinstance(item, str):
                    continue
                text = _sub_pre(item.strip(), MAX_QUESTION_ITEM_CHARS)
                if text and text not in seen:
                    seen.add(text)
                    result.append(text)
            if not result:
                return RecommendedQuestionsPayload.empty()
            return RecommendedQuestionsPayload.success(result[:count])
        except Exception:  # noqa: BLE001 —— 非法 JSON 视为无结果
            logger.warning("解析推荐追问问题失败，原文：%s", str(raw)[:200])
            return RecommendedQuestionsPayload.failed()


class RecommendedQuestionService:
    """推荐追问问题服务（对应 Java RecommendedQuestionServiceImpl）"""

    def __init__(self, message_dao: MessageDao, generator: RecommendedQuestionGenerator):
        self._message_dao = message_dao
        self._generator = generator

    async def generate(self, message_id: str, user_id: str) -> RecommendedQuestionsPayload:
        """生成推荐追问并落库（对齐 Java generate）"""
        message = self._load_assistant_message(message_id, user_id)
        if self._is_recommendation_disabled(message):
            return RecommendedQuestionsPayload.empty()

        cached = message.get("recommended_questions")
        if cached is not None:
            return RecommendedQuestionsPayload.success(_as_string_list(cached))

        question = self._load_question(message)
        answer = CitationMarkup.strip(message.get("content"))
        chunks = _parse_grounding_chunks(message.get("retrieved_chunks"))
        generated = await self._generator.generate(question, answer, chunks)
        if generated.status == RecommendedQuestionsStatus.FAILED:
            return generated

        # SUCCESS 与 EMPTY 都落库（空数组作为有效的负缓存）
        self._message_dao.update_recommended_questions(message_id, generated.questions)
        return generated

    # ==================== 内部辅助 ====================

    def _load_assistant_message(self, message_id: str, user_id: str) -> dict:
        """定位 assistant 消息并校验归属（他人消息或非 assistant 一律视为不存在，对齐 Java loadAssistantMessage）"""
        message = self._message_dao.find_by_id(message_id)
        if message is None:
            raise ClientException("消息不存在")
        if message.get("user_id") != user_id:
            raise ClientException("消息不存在")
        if str(message.get("role") or "").lower() != "assistant":
            raise ClientException("消息不存在")
        return message

    def _load_question(self, message: dict) -> Optional[str]:
        """通过 reply_to_message_id 取当前答案对应的用户提问（对齐 Java loadQuestion）"""
        reply_to = message.get("reply_to_message_id")
        if not reply_to or not str(reply_to).strip():
            return None
        q = self._message_dao.find_by_id(str(reply_to))
        if q is None:
            return None
        if q.get("conversation_id") != message.get("conversation_id"):
            return None
        if q.get("user_id") != message.get("user_id"):
            return None
        if str(q.get("role") or "").lower() != "user":
            return None
        return q.get("content")

    @staticmethod
    def _is_recommendation_disabled(message: dict) -> bool:
        """messageStatus 非空且非 NORMAL（如 INTERRUPTED）→ 不推荐（对齐 Java isRecommendationDisabled）"""
        status = message.get("message_status")
        return status is not None and status != _STATUS_NORMAL


# ==================== 工具函数（对齐 Java StrUtil.subPre / CitationMarkup） ====================


def _sub_pre(text: str, max_len: int) -> str:
    """取前 max_len 字符（对齐 Java StrUtil.subPre）"""
    return text[:max_len]


def _strip_code_fence(raw: str) -> str:
    """去除可能的 markdown 代码围栏（```json ... ``` 或 ``` ... ```，对齐 Java stripCodeFence）"""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
    return text


def _build_chunks_text(chunks: Optional[List[GroundingChunk]]) -> str:
    """拼装 grounding 片段文本供 prompt 注入（对齐 Java buildChunksText）；无片段降级提示语"""
    if not chunks:
        return "（无检索片段，仅依据问答生成）"
    parts = []
    total = 0
    idx = 1
    for chunk in chunks:
        if chunk is None or not (chunk.text or "").strip():
            continue
        prefix = f"{idx}. 【{chunk.doc_name or ''}】"
        remaining = MAX_CHUNKS_CHARS - total - len(prefix) - 1
        if remaining <= 0:
            break
        text = _sub_pre(chunk.text or "", remaining)
        parts.append(prefix + text)
        total += len(prefix) + len(text) + 1
        idx += 1
    if not parts:
        return "（无检索片段，仅依据问答生成）"
    return "\n".join(parts).rstrip()


def _parse_grounding_chunks(value) -> List[GroundingChunk]:
    """DB retrieved_chunks（JSONB）→ GroundingChunk 列表（兼容 dict 或 GroundingChunk 对象 / JSON 字符串）"""
    items = value
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:  # noqa: BLE001
            items = None
    # 加固：仅接受 list（JSON 解析出 dict/标量 或手工脏数据一律视为无片段）
    if not isinstance(items, list):
        return []
    chunks = []
    for item in items:
        if isinstance(item, GroundingChunk):
            chunks.append(item)
        elif isinstance(item, dict):
            chunks.append(GroundingChunk(
                doc_name=item.get("docName") or item.get("doc_name"),
                text=item.get("text"),
            ))
    return chunks


def _as_string_list(value) -> List[str]:
    """recommended_questions（JSONB）归一为 str 列表（兼容 list / JSON 字符串，非列表输入返回 []）"""
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:  # noqa: BLE001 —— 非法 JSON 视为空
            parsed = None
        # 加固：JSON 解析结果非 list（dict/标量）一律为空
        items = parsed if isinstance(parsed, list) else []
    else:
        return []
    # 仅收字符串元素（脏数据其它类型忽略）
    return [x for x in items if isinstance(x, str)]