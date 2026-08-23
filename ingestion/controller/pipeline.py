# -*- coding: utf-8 -*-
"""
ingestion.controller.pipeline - 摄取流水线端点（对应 Java IngestionPipelineController，P1-P5）

    P1  POST   /ingestion/pipelines              创建
    P2  PUT    /ingestion/pipelines/{id}         更新（name/description/nodes，无启停语义）
    P3  GET    /ingestion/pipelines/{id}         详情
    P4  GET    /ingestion/pipelines              分页（pageNo/pageSize/keyword）
    P5  DELETE /ingestion/pipelines/{id}         删除（软删 + 节点物理删）

Controller 只做「取服务 + 统一 Result 包裹 + camelize」薄转换。

对应 ragent 源码：
    - ingestion/controller/IngestionPipelineController
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from ingestion.controller.reqvo import (
    IngestionPipelineCreateRequest,
    IngestionPipelineUpdateRequest,
)
from rag.controller.vo import camelize

router = APIRouter(prefix="/ingestion/pipelines", tags=["ingestion-pipelines"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.post("", name="create_pipeline")
def create_pipeline(request: Request, body: IngestionPipelineCreateRequest) -> dict:
    """P1：创建流水线"""
    service = _container(request).ingestion_pipeline_service
    vo = service.create(
        body.name,
        body.description,
        [_node_dict(n) for n in body.nodes],
    )
    return result_to_dict(Results.success(camelize(vo)))


@router.put("/{pipeline_id}", name="update_pipeline")
def update_pipeline(request: Request, pipeline_id: str,
                    body: IngestionPipelineUpdateRequest) -> dict:
    """P2：更新流水线（name/description/nodes 部分更新）"""
    service = _container(request).ingestion_pipeline_service
    vo = service.update(
        pipeline_id,
        name=body.name,
        description=body.description,
        nodes=None if body.nodes is None else [_node_dict(n) for n in body.nodes],
    )
    return result_to_dict(Results.success(camelize(vo)))


@router.get("/{pipeline_id}", name="get_pipeline")
def get_pipeline(request: Request, pipeline_id: str) -> dict:
    """P3：流水线详情"""
    service = _container(request).ingestion_pipeline_service
    return result_to_dict(Results.success(camelize(service.get(pipeline_id))))


@router.get("", name="page_pipelines")
def page_pipelines(request: Request, pageNo: int = 1, pageSize: int = 10,
                   keyword: str | None = None) -> dict:
    """P4：流水线分页"""
    service = _container(request).ingestion_pipeline_service
    page = service.page(current=pageNo, size=pageSize, keyword=keyword)
    return result_to_dict(Results.success(camelize(page)))


@router.delete("/{pipeline_id}", name="delete_pipeline")
def delete_pipeline(request: Request, pipeline_id: str) -> dict:
    """P5：删除流水线"""
    service = _container(request).ingestion_pipeline_service
    service.delete(pipeline_id)
    return result_to_dict(Results.success())


def _node_dict(node) -> dict:
    return {
        "nodeId": node.nodeId,
        "nodeType": node.nodeType,
        "nextNodeId": node.nextNodeId,
        "settings": node.settings,
        "condition": node.condition,
    }
