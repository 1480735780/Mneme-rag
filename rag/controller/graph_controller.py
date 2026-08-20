# -*- coding: utf-8 -*-
"""
rag.controller.graph_controller - 知识图谱可视化端点（对应 Java GraphController，C12）

    - GET /admin/kg/graph   拉取子图（entity/collection/doc/depth/limit）
    - GET /admin/kg/labels  检索实体标签（keyword/limit）

委托既有 GraphQueryService（异步）；图谱通道未启用时服务层抛业务异常由 D0.8 转码。
方案 B：camelize 边界转换。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.GraphController
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(prefix="/admin/kg", tags=["graph"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("/graph", name="get_kg_graph")
async def get_kg_graph(
    request: Request,
    entity: Optional[str] = Query(default=None),
    collection: Optional[str] = Query(default=None),
    doc: Optional[str] = Query(default=None),
    depth: int = Query(default=2),
    limit: int = Query(default=200),
) -> dict:
    """GET /admin/kg/graph：图谱子图（entity 空取全图；doc 优先级高于 collection）"""
    container = _container(request)
    graph = await container.graph_service.get_graph(entity, collection, doc, depth, limit)
    return result_to_dict(Results.success(camelize(graph)))


@router.get("/labels", name="list_kg_labels")
async def list_kg_labels(
    request: Request,
    keyword: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
) -> dict:
    """GET /admin/kg/labels：实体标签（keyword 空取热门）"""
    container = _container(request)
    labels = await container.graph_service.search_entities(keyword, limit)
    return result_to_dict(Results.success(camelize(labels)))