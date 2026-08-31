# -*- coding: utf-8 -*-
"""
agent.controller - Agent 引擎 HTTP 端点（对应 Java 三个控制器）

三个路由器（factory 在 agent_engine_chat_service 装配完成后一并条件挂载，
对齐 Java @ConditionalOnAgentEngine 的引擎条件注册；workflow 模式不可达 = 决策 3B）：
    - chat_router:         GET /agent/v1/chat（SSE 流式对话）+ POST /agent/v1/stop
    - conversation_router: /agent/v1/conversations 最小 CRUD（与 workflow 会话接口两套分立）
    - meta_router:         GET /agent/v1/meta（引擎探活与身份，前端进聊天页先拉一次，绝不带密钥）

约定：
    - question 校验对齐 @ChatQuestion（NotBlank + 500 上限），违规 raise ClientException →
      全局异常处理器转 CLIENT_ERROR Result（对应 Java ConstraintViolationException 分支）；
    - query 参数 snake_case（conversation_id / task_id，对齐 mneme-rag 前端既有约定——
      workflow /rag/v3/chat 同口径）；
    - SSE 通道复用 workflow 的 SseQueue + StreamingResponse 设施（no-cache / X-Accel-Buffering /
      keep-alive 防代理 buffer），帧协议走 AgentSSEEventType 七类（与 workflow 两套分立）；
    - 会话/消息列表返回 service 层已组织的 camelCase dict（对齐 AgentConversationVO /
      AgentMessageVO 字段面）；
    - meta 能力清单与 mcpConfigured 同源：mcp_tool_count() > 0 才报 mcp-tools（对齐 Java
      「否则与 mcpConfigured 各说各话」的口径）。

偏离登记：
    - （已销案，R-B 2026-08-30）stop 属主复核已移植：cancel_by_user 与 Redis 属主比对，
      广播载荷 `taskId|requester`、执行端复核对齐 Java cancelByUser/cancelLocal。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.agent.controller.AgentChatController
    - com.nageoffer.ai.ragent.agent.controller.AgentConversationController
    - com.nageoffer.ai.ragent.agent.controller.AgentMetaController
    - com.nageoffer.ai.ragent.framework.validation.ChatQuestion
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.wiring import AppContainer
from common.context.user_context import UserContext
from common.exception.business import ClientException
from common.response.result import Results
from common.web import SseQueue
from common.web.serializer import result_to_dict
from agent.service import AgentSseSender

# @ChatQuestion.MAX_LENGTH：上限按 GET 查询串取（中文 URL 编码后约 9 字节一字，先撞容器请求头上限）
QUESTION_MAX_LENGTH = 500

chat_router = APIRouter(tags=["agent-engine"])
conversation_router = APIRouter(prefix="/agent/v1/conversations", tags=["agent-engine"])
meta_router = APIRouter(tags=["agent-engine"])


def _container(request: Request) -> AppContainer:
    """从应用状态取装配容器（service 经此注入）"""
    return request.app.state.container


def _validate_question(question: str) -> str:
    """@ChatQuestion 等效校验：NotBlank + 500 上限（违规 ClientException → CLIENT_ERROR Result）"""
    trimmed = (question or "").strip()
    if not trimmed:
        raise ClientException("问题不能为空")
    if len(trimmed) > QUESTION_MAX_LENGTH:
        raise ClientException(f"问题过长，最多 {QUESTION_MAX_LENGTH} 字")
    return trimmed


async def _stream_response(queue: SseQueue):
    """消费 SSE 队列；**响应结束（含客户端断开/异常）时关闭队列**（emitter 结束信号）

    - 正常完成：bridge 已在收尾路关闭队列，此处幂等；
    - 客户端断开：ASGI 取消响应任务 → 生成器被 aclose → finally 关闭队列，
      对齐 Java emitter.onCompletion/onTimeout/onError 的 recycleUpstream 取消上游。
    """
    try:
        async for frame in queue.aiter():
            yield frame
    finally:
        queue.close()


# ==================== 流式对话（AgentChatController）====================


@chat_router.get("/agent/v1/chat", name="agent_engine_chat")
async def agent_chat(
    request: Request,
    question: str,
    conversation_id: Optional[str] = None,
) -> StreamingResponse:
    """GET /agent/v1/chat：Agent SSE 流式对话（对齐 Java chat）

    stream_chat 同步返回（闸门 → 会话落库 → ReAct 循环后台跑），SSE 帧：
    meta → message/think/tool/hint → finish → done（AgentSSEEventType）。
    """
    container = _container(request)
    trimmed = _validate_question(question)
    queue = SseQueue()
    await container.agent_engine_chat_service.stream_chat(
        trimmed, UserContext.get_user_id(), conversation_id, AgentSseSender(queue)
    )
    # P3 Phase 0：SSE 防代理 buffer（与 workflow /rag/v3/chat 同一套响应头）
    return StreamingResponse(
        _stream_response(queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@chat_router.post("/agent/v1/stop", name="agent_engine_stop")
async def stop_agent(task_id: str, request: Request) -> dict:
    """POST /agent/v1/stop：停止指定 Agent 流式任务（对齐 Java stop → cancelByUser 属主复核）

    Java 端点无 @IdempotentSubmit（与 workflow /rag/v3/stop 不同），重复停止由
    task_manager.cancel_local 的 CAS 防重承接（onCancelSupplier 只执行一次）；
    R-B：发起方经 cancel_by_user 与 Redis 属主比对（越权 → ClientException）。
    """
    container = _container(request)
    await container.agent_engine_chat_service.stop_task(task_id, UserContext.get_user_id())
    return result_to_dict(Results.success(None))


# ==================== 会话 CRUD（AgentConversationController）====================


class AgentTitleRequest(BaseModel):
    """PUT title 请求体（对应 Java TitleRequest{title}）"""

    title: str


class AgentBatchDeleteRequest(BaseModel):
    """POST batch-delete 请求体（对应 Java BatchDeleteRequest{ids}）"""

    ids: List[str] = Field(default_factory=list)


@conversation_router.get("", name="agent_conversations")
async def list_agent_conversations(request: Request) -> dict:
    """GET /agent/v1/conversations：当前用户会话列表（last_time 倒序 + 轮数）"""
    container = _container(request)
    rows = container.agent_engine_conversation_service.list_by_user(UserContext.get_user_id())
    return result_to_dict(Results.success(rows))


@conversation_router.get("/{conversation_id}/messages", name="agent_conversation_messages")
async def list_agent_messages(conversation_id: str, request: Request) -> dict:
    """GET /agent/v1/conversations/{id}/messages：会话消息历史（含 blocks 轨迹，ASC）"""
    container = _container(request)
    rows = container.agent_engine_conversation_service.list_messages(
        conversation_id, UserContext.get_user_id()
    )
    return result_to_dict(Results.success(rows))


@conversation_router.put("/{conversation_id}/title", name="agent_rename_conversation")
async def rename_agent_conversation(
    conversation_id: str,
    payload: AgentTitleRequest,
    request: Request,
) -> dict:
    """PUT /agent/v1/conversations/{id}/title：重命名（空标题/会话不存在 → ClientException）"""
    container = _container(request)
    container.agent_engine_conversation_service.rename(
        conversation_id, UserContext.get_user_id(), payload.title
    )
    return result_to_dict(Results.success(None))


@conversation_router.delete("/{conversation_id}", name="agent_delete_conversation")
async def delete_agent_conversation(conversation_id: str, request: Request) -> dict:
    """DELETE /agent/v1/conversations/{id}：软删会话 + 释放运行态"""
    container = _container(request)
    container.agent_engine_conversation_service.delete(
        conversation_id, UserContext.get_user_id()
    )
    return result_to_dict(Results.success(None))


@conversation_router.post("/batch-delete", name="agent_batch_delete_conversations")
async def batch_delete_agent_conversations(payload: AgentBatchDeleteRequest, request: Request) -> dict:
    """POST /agent/v1/conversations/batch-delete：批量软删（ids 逐个走单删语义）"""
    container = _container(request)
    container.agent_engine_conversation_service.delete_batch(
        payload.ids, UserContext.get_user_id()
    )
    return result_to_dict(Results.success(None))


# ==================== 引擎元信息（AgentMetaController）====================


@meta_router.get("/agent/v1/meta", name="agent_meta")
async def agent_meta(request: Request) -> dict:
    """GET /agent/v1/meta：引擎探活与身份（framework/model/maxIters/capabilities/toolProvider）"""
    container = _container(request)
    properties = container.agent_engine_properties
    mcp_configured = container.agent_engine_tool_catalog.mcp_tool_count() > 0
    capabilities = ["react", "knowledge-base"]
    if mcp_configured:
        capabilities.append("mcp-tools")
    return result_to_dict(Results.success({
        "framework": "AgentScope ReAct",
        "model": properties.chat_model,
        "maxIters": properties.max_iters,
        "capabilities": capabilities,
        "toolProvider": "native + mcp" if mcp_configured else "native",
        "mcpConfigured": mcp_configured,
    }))
