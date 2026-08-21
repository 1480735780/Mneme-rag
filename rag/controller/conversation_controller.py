# -*- coding: utf-8 -*-
"""
rag.controller.conversation_controller - 会话 REST 端点（对应 Java ConversationController）

会话域切片（2.5）：会话列表 / 重命名 / 删除 / 消息历史，对齐 Java 端点：
    - GET    /conversations/{conversationId}/messages  （listMessages，ASC 全量）
    - PUT    /conversations/{conversationId}           （rename，body title）
    - DELETE /conversations/{conversationId}           （delete，级联软删）
    - GET    /conversations                            （listByUserId）

user_id 取自 UserContext（由 UserContextMiddleware 从 X-User-Id 头解析，P4 决策 D3）；
未带用户头时兜底 `anonymous`（对齐 D3）。返回统一 Result（HTTP 200 包裹，对齐 Java Results.success）。

依赖注入：service 从 `request.app.state.container` 取（AppContainer 由 create_app lifespan 装配）。
异常（ClientException：会话不存在/名称超长）由 D0.8 全局异常处理器统一转 Result。

端点路径经 APIRouter 挂到 /conversations 前缀。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.ConversationController
    - com.nageoffer.ai.ragent.rag.controller.request.ConversationUpdateRequest
    - com.nageoffer.ai.ragent.rag.controller.vo.ConversationVO / ConversationMessageVO
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.wiring import AppContainer
from common.context.user_context import UserContext
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.dao.message_dao import MessageOrder
from rag.controller.vo import ConversationMessageVO, ConversationVO

router = APIRouter(prefix="/conversations", tags=["conversation"])


# 会话更新请求（对应 Java ConversationUpdateRequest{title}）
class ConversationUpdateRequest(BaseModel):
    title: str


def _container(request: Request) -> AppContainer:
    """从应用状态取装配容器（service 经此注入）"""
    return request.app.state.container


# ==================== 会话列表 ====================


@router.get("", name="list_conversations")
async def list_conversations(request: Request) -> dict:
    """GET /conversations：当前用户会话列表（last_time 倒序，ConversationVO camelCase）"""
    container = _container(request)
    rows = container.conversation_service.list_by_user(UserContext.get_user_id())
    data = [ConversationVO.from_row(r).to_camel_dict() for r in rows]
    return result_to_dict(Results.success(data))


# ==================== 重命名 / 删除 ====================


@router.put("/{conversation_id}", name="rename_conversation")
async def rename_conversation(
    conversation_id: str,
    payload: ConversationUpdateRequest,
    request: Request,
) -> dict:
    """PUT /conversations/{id}：重命名会话（title 校验，超长/空抛 ClientException）"""
    container = _container(request)
    container.conversation_service.rename(
        conversation_id, UserContext.get_user_id(), payload.title
    )
    return result_to_dict(Results.success(None))


@router.delete("/{conversation_id}", name="delete_conversation")
async def delete_conversation(conversation_id: str, request: Request) -> dict:
    """DELETE /conversations/{id}：删除会话（级联软删会话+消息+摘要）"""
    container = _container(request)
    container.conversation_service.delete(conversation_id, UserContext.get_user_id())
    return result_to_dict(Results.success(None))


# ==================== 消息历史 ====================


@router.get("/{conversation_id}/messages", name="list_conversation_messages")
async def list_conversation_messages(
    conversation_id: str,
    request: Request,
    limit: Optional[int] = None,
) -> dict:
    """GET /conversations/{id}/messages：会话消息历史（ASC 时间正序全量，ConversationMessageVO camelCase）"""
    container = _container(request)
    rows = container.message_service.list_messages(
        conversation_id, UserContext.get_user_id(), limit=limit, order=MessageOrder.ASC
    )
    data = [ConversationMessageVO.from_row(r).to_camel_dict() for r in rows]
    return result_to_dict(Results.success(data))