# -*- coding: utf-8 -*-
"""
knowledge.controller.document - 文档端点（对应 Java KnowledgeDocumentController，D1-D12）

    D1  GET /knowledge-base/docs/ingestion-spec-schema    表单 schema
    D2  POST /knowledge-base/{kb-id}/docs/upload          上传（multipart: file + form 字段）
    D3  POST /knowledge-base/docs/{doc-id}/chunk          开始分块
    D4  DELETE /knowledge-base/docs/{doc-id}              删除
    D5  GET /knowledge-base/docs/{docId}                  详情
    D6  PUT /knowledge-base/docs/{docId}                  更新
    D7  GET /knowledge-base/{kb-id}/docs                  分页
    D8  GET /knowledge-base/docs/search                   搜索
    D9  PATCH /knowledge-base/docs/{docId}/enable         启用/禁用
    D10 GET /knowledge-base/docs/{docId}/chunk-logs       分块日志分页
    D11 GET /knowledge-base/docs/{docId}/preview          markdown 预览（String data）
    D12 GET /knowledge-base/docs/{docId}/file             源文件流（StreamingResponse）

Controller 层只做「取服务 + 统一 Result 包裹 + camelize/VO 投影」薄转换；D12 为非 JSON 响应。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.controller.KnowledgeDocumentController
"""
from __future__ import annotations

from typing import IO, Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from knowledge.controller.reqvo import KnowledgeDocumentUpdateRequest
from rag.controller.vo import camelize

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base-docs"])

# Java KnowledgeDocumentVO 投影键（核对自 KnowledgeDocumentVO.java：无 mimeType；有 updatedBy；
# chunksEdited 仅查询时填充）
_DOC_VO_KEYS = (
    "id", "kb_id", "doc_name", "source_type", "source_location", "schedule_enabled",
    "schedule_cron", "enabled", "chunk_count", "file_url", "file_type",
    "file_size", "process_mode", "ingestion_spec", "pipeline_id", "status",
    "created_by", "updated_by", "create_time", "update_time",
)

# Java KnowledgeDocumentSearchVO 投影键（核对自 KnowledgeDocumentSearchVO.java：仅 4 字段）
_SEARCH_VO_KEYS = ("id", "kb_id", "doc_name")

# 上传大小上限（对齐 Java spring.servlet.multipart.max-file-size:50MB 的容器层拦截）
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# 文件扩展名 → Content-Type（对齐 Java CONTENT_TYPE_MAP）
_CONTENT_TYPE_MAP = {
    "pdf": "application/pdf",
    "markdown": "text/markdown",
    "md": "text/markdown",
    "txt": "text/plain",
    "csv": "text/csv;charset=utf-8",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
}


def _container(request: Request) -> AppContainer:
    return request.app.state.container


def _project(row: dict) -> dict:
    """行 → VO 投影（page 行附带的 chunks_edited / search 行附带的 kb_name 按需保留）"""
    vo = {k: row.get(k) for k in _DOC_VO_KEYS}
    if "chunks_edited" in row:
        vo["chunks_edited"] = row["chunks_edited"]
    if "kb_name" in row:
        vo["kb_name"] = row["kb_name"]
    return vo


def _project_page(page: dict) -> dict:
    page["records"] = [_project(r) for r in page["records"]]
    return page


@router.get("/docs/ingestion-spec-schema", name="get_ingestion_spec_schema")
async def get_ingestion_spec_schema(request: Request) -> dict:
    """D1：摄取配置表单 schema（ingestionSpec 字段的渲染描述）"""
    container = _container(request)
    schema = container.ingestion_spec_schema_provider.describe()
    return result_to_dict(Results.success(camelize(schema)))


@router.post("/{kb_id}/docs/upload", name="upload_document")
async def upload_document(
    request: Request,
    kb_id: str,
    file: Optional[UploadFile] = File(default=None),
    sourceType: Optional[str] = Form(default=None),
    sourceLocation: Optional[str] = Form(default=None),
    scheduleEnabled: Optional[bool] = Form(default=None),
    scheduleCron: Optional[str] = Form(default=None),
    processMode: Optional[str] = Form(default=None),
    ingestionSpec: Optional[str] = Form(default=None),
    pipelineId: Optional[str] = Form(default=None),
) -> dict:
    """D2：上传文档（multipart：file + 表单字段，字段名对齐 Java camelCase）"""
    container = _container(request)
    if file is not None and file.size is not None and file.size > _MAX_UPLOAD_BYTES:
        # 对齐 Java multipart max-file-size 的容器层拦截；超限在读取前拒绝（不入内存）
        from common.exception.business import ClientException

        raise ClientException(f"上传文件大小超过限制: {_MAX_UPLOAD_BYTES} bytes")
    vo = await container.knowledge_document_service.upload(
        kb_id,
        source_type=sourceType,
        source_location=sourceLocation,
        schedule_enabled=scheduleEnabled,
        schedule_cron=scheduleCron,
        process_mode=processMode,
        ingestion_spec=ingestionSpec,
        pipeline_id=pipelineId,
        file_content=await file.read() if file is not None else None,
        file_name=file.filename if file is not None else None,
        content_type=file.content_type if file is not None else None,
    )
    return result_to_dict(Results.success(camelize(_project(vo))))


@router.post("/docs/{doc_id}/chunk", name="start_chunk")
async def start_document_chunk(doc_id: str, request: Request) -> dict:
    """D3：开始分块（CAS 防重）"""
    container = _container(request)
    await container.knowledge_document_service.start_chunk(doc_id)
    return result_to_dict(Results.success())


@router.delete("/docs/{doc_id}", name="delete_document")
async def delete_document(doc_id: str, request: Request) -> dict:
    """D4：删除文档（RUNNING 拒删）"""
    container = _container(request)
    await container.knowledge_document_service.delete(doc_id)
    return result_to_dict(Results.success())


@router.get("/docs/search", name="search_documents")
async def search_documents(
    request: Request,
    keyword: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=8),
) -> dict:
    """D8：全局文档搜索（doc_name like）+ kb_name 回填（须在 /docs/{doc_id} 之前注册，避免被参数路由吞掉）

    出参对齐 Java KnowledgeDocumentSearchVO：仅 id/kbId/docName/kbName 四字段。
    """
    container = _container(request)
    rows = container.knowledge_document_service.search(keyword, limit)
    slim = [{**{k: r.get(k) for k in _SEARCH_VO_KEYS}, "kb_name": r.get("kb_name")} for r in rows]
    return result_to_dict(Results.success(camelize(slim)))


@router.get("/docs/{doc_id}", name="get_document")
async def get_document(doc_id: str, request: Request) -> dict:
    """D5：文档详情"""
    container = _container(request)
    row = container.knowledge_document_service.get(doc_id)
    return result_to_dict(Results.success(camelize(_project(row))))


@router.put("/docs/{doc_id}", name="update_document")
async def update_document(doc_id: str, request: Request, body: KnowledgeDocumentUpdateRequest) -> dict:
    """D6：更新文档"""
    container = _container(request)
    container.knowledge_document_service.update(
        doc_id,
        doc_name=body.doc_name, process_mode=body.process_mode, ingestion_spec=body.ingestion_spec,
        pipeline_id=body.pipeline_id, source_location=body.source_location,
        schedule_enabled=body.schedule_enabled, schedule_cron=body.schedule_cron,
    )
    return result_to_dict(Results.success())


@router.get("/{kb_id}/docs", name="page_documents")
async def page_documents(
    kb_id: str,
    request: Request,
    keyword: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
) -> dict:
    """D7：文档分页（keyword/status 过滤）"""
    container = _container(request)
    page = container.knowledge_document_service.page(
        kb_id, keyword=keyword, status=status, current=current, size=size
    )
    return result_to_dict(Results.success(camelize(_project_page(page))))


@router.patch("/docs/{doc_id}/enable", name="enable_document")
async def enable_document(doc_id: str, request: Request, value: bool = Query(...)) -> dict:
    """D9：启用/禁用文档（enable 双向向量同步；N3 chunk_service 注入后重嵌入生效）"""
    container = _container(request)
    await container.knowledge_document_service.enable(doc_id, value)
    return result_to_dict(Results.success())


@router.get("/docs/{doc_id}/chunk-logs", name="get_document_chunk_logs")
async def get_document_chunk_logs(
    doc_id: str,
    request: Request,
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
) -> dict:
    """D10：分块日志分页（含 other_duration 计算）"""
    container = _container(request)
    page = container.knowledge_document_service.get_chunk_logs(doc_id, current=current, size=size)
    return result_to_dict(Results.success(camelize(page)))


@router.get("/docs/{doc_id}/preview", name="preview_document")
async def preview_document(doc_id: str, request: Request) -> dict:
    """D11：markdown 预览（String data）"""
    container = _container(request)
    text = container.knowledge_document_service.preview(doc_id)
    return result_to_dict(Results.success(text))


@router.get("/docs/{doc_id}/file", name="document_file")
async def document_file(doc_id: str, request: Request):
    """D12：源文件流式返回（浏览器原生渲染）"""
    container = _container(request)
    doc = container.knowledge_document_service.get(doc_id)
    file_type = (doc.get("file_type") or "").lower()
    content_type = _CONTENT_TYPE_MAP.get(file_type, "application/octet-stream")
    stream: IO = container.knowledge_document_service.file(doc_id)
    # 文件名用户可控：先剔 CR/LF 防 header 注入，再 URL 编码（对齐 Java URLEncoder.encode）
    raw_name = (doc.get("doc_name") or "file").replace("\r", "").replace("\n", "")
    encoded = quote_plus(raw_name)
    return StreamingResponse(stream, media_type=content_type,
                             headers={"Content-Disposition": f'inline; filename="{encoded}"'})