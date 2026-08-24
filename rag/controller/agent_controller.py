# -*- coding: utf-8 -*-
"""
rag.controller.agent_controller - Agent 对话端点（POST /agent/chat）

JSON（非流式）：question + 可选 history → camelCase AgentResult
（answer / steps / iterations / error）。Agent 闭环天然多轮同步，MVP 不做 SSE 流式。
依赖注入：agent_service 从 request.app.state.container 取（wiring 装配；引擎未就绪不挂载）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(tags=["agent"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.post("/agent/chat", name="agent_chat")
async def agent_chat(
    request: Request,
    payload: Dict[str, Any] = Body(...),
) -> dict:
    """POST /agent/chat：Agent 闭环（plan-execute-observe-answer）"""
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    history: Optional[List[Dict[str, Any]]] = payload.get("history")
    container = _container(request)
    data = await container.agent_service.chat(question, history)
    return result_to_dict(Results.success(camelize(data)))
