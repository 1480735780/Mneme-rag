# -*- coding: utf-8 -*-
"""
rag.controller.recommended_question_controller - 推荐追问问题 REST 端点（对应 Java RecommendedQuestionController）

推荐追问域切片（C5，方案 B 重建）：
    - POST /conversations/messages/{id}/recommended-questions   生成推荐追问问题并落库

答案完成后按需触发，POST 幂等生成（命中已有 recommended_questions 直接返回），不占用 chat 流式关键路径。
返回 RecommendedQuestionsPayload 的 camelCase dict `{status, questions}`（status 取枚举 .value）。

ClientException（消息不存在）由 D0.8 全局异常处理器转码。
user_id 取自 UserContext（X-User-Id 头）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.RecommendedQuestionController
    - com.nageoffer.ai.ragent.rag.dto.RecommendedQuestionsPayload
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.wiring import AppContainer
from common.context.user_context import UserContext
from common.response.result import Results
from common.web.serializer import result_to_dict

router = APIRouter(prefix="/conversations/messages", tags=["recommended-question"])


def _container(request: Request) -> AppContainer:
    """从应用状态取装配容器（service 经此注入）"""
    return request.app.state.container


@router.post("/{message_id}/recommended-questions", name="generate_recommended_questions")
async def generate_recommended_questions(message_id: str, request: Request) -> dict:
    """POST /conversations/messages/{id}/recommended-questions：生成推荐追问问题并落库"""
    container = _container(request)
    payload = await container.recommended_question_service.generate(
        message_id, UserContext.get_user_id()
    )
    return result_to_dict(Results.success(_payload_to_dict(payload)))


def _payload_to_dict(payload) -> dict:
    """RecommendedQuestionsPayload → camelCase JSON dict（status 取枚举值，对齐 Java record 序列化）"""
    return {
        "status": payload.status.value,
        "questions": payload.questions,
    }