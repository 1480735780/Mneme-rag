# -*- coding: utf-8 -*-
"""
rag.controller.eval_controller - 评测检索端点（对应 Java EvalController）

    - GET /rag/eval?question=  纯检索证据（无 LLM 输出）：docIds/chunkIds/contexts/
      contextDocIds/mcpContext/subIntents/intentLeafIds/latencyMs（camelCase VO）

factory 按 eval_enabled 条件挂载（D5）；eval_service 由 wiring 在引擎就绪时构建（D9 前置：
评测环境须 LLM 就绪 + 检索通道启用）。边界经 camelize 递归转 camelCase（对齐 Java EvalResponse）。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(tags=["eval"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("/rag/eval", name="eval_retrieval")
async def eval_retrieval(request: Request, question: str = Query(...)) -> dict:
    """GET /rag/eval：评测检索证据（改写→意图→检索→两跳 docId 解析）"""
    container = _container(request)
    data = await container.eval_service.load_eval(question)
    return result_to_dict(Results.success(camelize(data)))
