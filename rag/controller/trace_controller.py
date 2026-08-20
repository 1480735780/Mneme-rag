# -*- coding: utf-8 -*-
"""
rag.controller.trace_controller - 追踪查询端点（对应 Java RagTraceController，C7）

    - GET /rag/traces/runs                分页查询运行记录（current/size/traceId/conversationId/taskId/status）
    - GET /rag/traces/runs/{traceId}      详情（含节点）；不存在返回 null data
    - GET /rag/traces/runs/{traceId}/nodes 节点列表

方案 B：service 返回 snake_case dict，边界经 `camelize` 转 camelCase。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.RagTraceController
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(prefix="/rag/traces/runs", tags=["trace"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("", name="page_trace_runs")
async def page_trace_runs(
    request: Request,
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
    trace_id: Optional[str] = Query(default=None),
    conversation_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
) -> dict:
    """GET /rag/traces/runs：追踪运行分页（start_time 倒序 + 可选过滤）"""
    container = _container(request)
    page = container.trace_query_service.page_runs(
        current=current, size=size,
        trace_id=trace_id, conversation_id=conversation_id,
        task_id=task_id, status=status,
    )
    return result_to_dict(Results.success(camelize(page)))


@router.get("/{trace_id}", name="get_trace_detail")
async def get_trace_detail(trace_id: str, request: Request) -> dict:
    """GET /rag/traces/runs/{traceId}：追踪详情（含 nodes）；不存在返回 null data"""
    container = _container(request)
    detail = container.trace_query_service.detail(trace_id)
    return result_to_dict(Results.success(camelize(detail) if detail is not None else None))


@router.get("/{trace_id}/nodes", name="list_trace_nodes")
async def list_trace_nodes(trace_id: str, request: Request) -> dict:
    """GET /rag/traces/runs/{traceId}/nodes：追踪节点列表"""
    container = _container(request)
    nodes = container.trace_query_service.list_nodes(trace_id)
    return result_to_dict(Results.success(camelize(nodes)))