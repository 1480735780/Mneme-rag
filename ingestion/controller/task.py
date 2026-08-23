# -*- coding: utf-8 -*-
"""
ingestion.controller.task - 摄取任务端点（对应 Java IngestionTaskController，T1-T5）

    T1  POST   /ingestion/tasks              创建并执行任务（JSON source）
    T2  POST   /ingestion/tasks/upload       上传文件触发（multipart: pipelineId + file）
    T3  GET    /ingestion/tasks/{id}         任务详情
    T4  GET    /ingestion/tasks/{id}/nodes   任务节点运行记录
    T5  GET    /ingestion/tasks              分页（pageNo/pageSize/status）

T1/T2 为 async（引擎执行含网络/嵌入 IO）。Controller 只做「取服务 + 统一 Result 包裹 + camelize」。

对应 ragent 源码：
    - ingestion/controller/IngestionTaskController
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.wiring import AppContainer
from common.exception.business import ClientException
from common.response.result import Results
from common.web.serializer import result_to_dict
from ingestion.controller.reqvo import IngestionTaskCreateRequest
from ingestion.domain.context import DocumentSource
from ingestion.domain.enums import SourceType
from rag.controller.vo import camelize
from storage.vector.schema import VectorSpaceId

router = APIRouter(prefix="/ingestion/tasks", tags=["ingestion-tasks"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.post("", name="create_task")
async def create_task(request: Request, body: IngestionTaskCreateRequest) -> dict:
    """T1：创建并执行任务"""
    service = _container(request).ingestion_task_service
    source = _to_source(body)
    vector_space_id = _to_vector_space_id(body.vectorSpaceId)
    result = await service.execute(body.pipelineId, source, vector_space_id)
    return result_to_dict(Results.success(camelize(_result_payload(result))))


@router.post("/upload", name="upload_task")
async def upload_task(request: Request, pipelineId: str = Form(...),
                      file: UploadFile = File(...)) -> dict:
    """T2：上传文件并触发任务（对齐 Java @RequestPart file）"""
    service = _container(request).ingestion_task_service
    content = await file.read()
    result = await service.upload(pipelineId, content, file.filename)
    return result_to_dict(Results.success(camelize(_result_payload(result))))


@router.get("/{task_id}", name="get_task")
def get_task(request: Request, task_id: str) -> dict:
    """T3：任务详情"""
    service = _container(request).ingestion_task_service
    return result_to_dict(Results.success(camelize(service.get(task_id))))


@router.get("/{task_id}/nodes", name="list_task_nodes")
def list_task_nodes(request: Request, task_id: str) -> dict:
    """T4：任务节点运行记录"""
    service = _container(request).ingestion_task_service
    return result_to_dict(Results.success(camelize(service.list_nodes(task_id))))


@router.get("", name="page_tasks")
def page_tasks(request: Request, pageNo: int = 1, pageSize: int = 10,
               status: str | None = None) -> dict:
    """T5：任务分页"""
    service = _container(request).ingestion_task_service
    page = service.page(current=pageNo, size=pageSize, status=status)
    return result_to_dict(Results.success(camelize(page)))


def _to_source(body: IngestionTaskCreateRequest) -> DocumentSource:
    """请求 source → DocumentSource（类型校验对齐 Java toSource）"""
    raw_type = (body.source.type or "").strip()
    if not raw_type:
        raise ClientException("文档来源类型不能为空")
    try:
        source_type = SourceType.from_value(raw_type)
    except ValueError:
        raise ClientException(f"不支持的来源类型: {raw_type}")
    return DocumentSource(
        type=source_type,
        location=body.source.location,
        file_name=body.source.fileName,
        credentials=body.source.credentials,
    )


def _to_vector_space_id(raw: dict | None):
    if not raw:
        return None
    return VectorSpaceId(
        logical_name=(raw.get("logicalName") or "").strip(),
        namespace=raw.get("namespace"),
    )


def _result_payload(result) -> dict:
    """IngestionResult dataclass → camelCase dict"""
    return {
        "taskId": result.task_id,
        "pipelineId": result.pipeline_id,
        "status": result.status.value if result.status is not None else None,
        "chunkCount": result.chunk_count,
        "message": result.message,
    }
