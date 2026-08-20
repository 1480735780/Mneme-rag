# -*- coding: utf-8 -*-
"""
rag.controller.intent_tree_controller - 意图树管理端点（对应 Java IntentTreeController，C9）

    - GET  /intent-tree/trees            完整管理树
    - POST /intent-tree                  创建节点（返回新 ID）
    - PUT  /intent-tree/{id}             更新节点
    - DELETE /intent-tree/{id}           删除节点
    - POST /intent-tree/batch/enable|disable|delete   批量启停/软删（子树全包含校验）

方案 B：service 返回 snake_case，边界经 `camelize` 转 camelCase。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.IntentTreeController
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.request import (
    IntentNodeBatchRequest,
    IntentNodeCreateRequest,
    IntentNodeUpdateRequest,
)
from rag.controller.vo import camelize

router = APIRouter(prefix="/intent-tree", tags=["intent-tree"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("/trees", name="list_intent_tree")
async def list_intent_tree(request: Request) -> dict:
    container = _container(request)
    return result_to_dict(Results.success(camelize(container.intent_tree_admin_service.tree())))


@router.post("", name="create_intent_node")
async def create_intent_node(request: Request, payload: IntentNodeCreateRequest) -> dict:
    container = _container(request)
    nid = container.intent_tree_admin_service.create(**payload.model_dump())
    return result_to_dict(Results.success(nid))


@router.put("/{nid}", name="update_intent_node")
async def update_intent_node(
    nid: str, request: Request, payload: IntentNodeUpdateRequest
) -> dict:
    container = _container(request)
    container.intent_tree_admin_service.update(nid, **payload.model_dump())
    return result_to_dict(Results.success(None))


@router.delete("/{nid}", name="delete_intent_node")
async def delete_intent_node(nid: str, request: Request) -> dict:
    container = _container(request)
    container.intent_tree_admin_service.delete(nid)
    return result_to_dict(Results.success(None))


@router.post("/batch/enable", name="batch_enable_intent_nodes")
async def batch_enable_intent_nodes(request: Request, payload: IntentNodeBatchRequest) -> dict:
    container = _container(request)
    container.intent_tree_admin_service.batch_enable(payload.ids)
    return result_to_dict(Results.success(None))


@router.post("/batch/disable", name="batch_disable_intent_nodes")
async def batch_disable_intent_nodes(request: Request, payload: IntentNodeBatchRequest) -> dict:
    container = _container(request)
    container.intent_tree_admin_service.batch_disable(payload.ids)
    return result_to_dict(Results.success(None))


@router.post("/batch/delete", name="batch_delete_intent_nodes")
async def batch_delete_intent_nodes(request: Request, payload: IntentNodeBatchRequest) -> dict:
    container = _container(request)
    container.intent_tree_admin_service.batch_delete(payload.ids)
    return result_to_dict(Results.success(None))