# -*- coding: utf-8 -*-
"""
rag.controller.chat_controller - 聊天 SSE / 停止端点（对应 Java RAGChatController）

端点：
    - GET  /rag/v3/chat   流式问答（SSE：meta → message → finish → done）
    - POST /rag/v3/stop   停止指定任务（task_manager.cancel）

接线（对齐 Java RAGChatController）：
    - GET /chat 幂等：Java @IdempotentSubmit(key=userId, message=...)，Python 侧以
      idempotent_guard 包裹（key = userId value key），重复提交 raise ClientException；
    - user_id 取自 UserContext（UserContextMiddleware 从 X-User-Id 头解析，P4 决策 D3）；
    - 流式：端点建 SseQueue → 幂等包裹内调用 chat_service.stream_chat（同步返回，后台跑引擎）
      → 返回 StreamingResponse(queue.aiter(), media_type="text/event-stream")。

依赖注入：chat_service / idempotent_guard / stream_task_manager 从
`request.app.state.container` 取（AppContainer 装配；engine 交给 M7 C14 全链装配）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.RAGChatController
"""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.wiring import AppContainer
from common.context.user_context import UserContext
from common.response.result import Results
from common.web import SseQueue
from common.web.serializer import result_to_dict
from rag.service.idempotent import CHAT_SUBMIT_MESSAGE, IdempotentSubmitGuard

router = APIRouter(prefix="/rag/v3", tags=["chat"])


def _container(request: Request) -> AppContainer:
    """从应用状态取装配容器（service 经此注入）"""
    return request.app.state.container


# ==================== 流式问答 ====================


@router.get("/chat", name="rag_chat")
async def chat(
    request: Request,
    question: str,
    conversation_id: Optional[str] = None,
    deep_thinking: bool = False,
) -> StreamingResponse:
    """GET /rag/v3/chat：SSE 流式问答（对齐 Java chat）"""
    container = _container(request)
    user_id = UserContext.get_user_id()
    queue = SseQueue()

    # 幂等（对齐 Java @IdempotentSubmit key=userId；重复提交 raise ClientException -> 统一 Result）
    idempotent_key = IdempotentSubmitGuard.build_value_key(user_id)

    async def submit() -> Tuple[str, str]:
        # stream_chat 同步返回 (conversation_id, task_id) 且后台跑引擎，不阻塞本包裹
        return container.chat_service.stream_chat(
            question, conversation_id, deep_thinking, user_id, queue
        )

    await container.idempotent_guard.execute(idempotent_key, submit, message=CHAT_SUBMIT_MESSAGE)
    return StreamingResponse(queue.aiter(), media_type="text/event-stream")


# ==================== 停止任务 ====================


@router.post("/stop", name="rag_stop")
async def stop(task_id: str, request: Request) -> dict:
    """POST /rag/v3/stop：停止指定流式任务（对齐 Java stop + @IdempotentSubmit）

    stop 也作幂等（对齐 Java RAGChatController.stop 注解）：同 taskId 重复停止走 guard 防重；
    与 task_manager.cancel 的 CAS 防重（3.4）职责互补——guard 拦**请求层**重复提交，cancel CAS 拦任务态。

    锁窗口 = stop_task 时长（一次 Redis set + 本地广播，毫秒级）。
    """
    container = _container(request)

    async def do_stop() -> None:
        await container.chat_service.stop_task(task_id)

    # key=taskId（Java stop 注解未自定义 message，用默认），重复停止 raise ClientException
    await container.idempotent_guard.execute(
        IdempotentSubmitGuard.build_value_key(task_id), do_stop
    )
    return result_to_dict(Results.success(None))