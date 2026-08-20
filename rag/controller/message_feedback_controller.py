# -*- coding: utf-8 -*-
"""
rag.controller.message_feedback_controller - 消息反馈 REST 端点（对应 Java MessageFeedbackController）

反馈域切片（C4，方案 B 重建）：
    - POST   /conversations/messages/{id}/feedback   提交点赞/踩反馈（vote=1/-1，异步分发持久化）
    - DELETE /conversations/messages/{id}/feedback   取消点赞/踩反馈（异步分发，保留已有 vote）

提交/取消均为**异步**（构造 MessageFeedbackEvent → asyncio.create_task(submit_by_event)，D6 进程内等价 MQ），
HTTP 立即返回 Result.success()；落库在校验+消费侧完成。请求体经 controller 边界 pydantic
（rag.controller.request.MessageFeedbackRequest）解析后映射为 service 层事件 dataclass。
ClientException（反馈值必须为 1 或 -1 等）由 D0.8 全局异常处理器转码。

user_id 取自 UserContext（X-User-Id 头）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.MessageFeedbackController
    - com.nageoffer.ai.ragent.rag.controller.request.MessageFeedbackRequest
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.request import MessageFeedbackRequest as FeedbackBody
from rag.service.feedback_service import MessageFeedbackRequest as FeedbackEventRequest

router = APIRouter(prefix="/conversations/messages", tags=["feedback"])


def _container(request: Request) -> AppContainer:
    """从应用状态取装配容器（service 经此注入）"""
    return request.app.state.container


@router.post("/{message_id}/feedback", name="submit_feedback")
async def submit_feedback(
    message_id: str, request: Request, payload: FeedbackBody
) -> dict:
    """POST /conversations/messages/{id}/feedback：提交点赞/踩反馈（异步）"""
    container = _container(request)
    container.feedback_service.submit_feedback_async(
        message_id,
        FeedbackEventRequest(
            vote=payload.vote, reason=payload.reason, comment=payload.comment
        ),
    )
    return result_to_dict(Results.success(None))


@router.delete("/{message_id}/feedback", name="cancel_feedback")
async def cancel_feedback(message_id: str, request: Request) -> dict:
    """DELETE /conversations/messages/{id}/feedback：取消点赞/踩反馈（异步）"""
    container = _container(request)
    container.feedback_service.cancel_feedback_async(message_id)
    return result_to_dict(Results.success(None))