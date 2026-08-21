# -*- coding: utf-8 -*-
"""
knowledge.controller.chunk - 分块端点（对应 Java KnowledgeChunkController，C1-C6）

    C1  GET    /knowledge-base/docs/{doc-id}/chunks          分页（current/size/enabled）
    C2  POST   /knowledge-base/docs/{doc-id}/chunks          新增手工 Chunk
    C3  PUT    /knowledge-base/docs/{doc-id}/chunks/{chunk-id}  更新内容
    C4  DELETE /knowledge-base/docs/{doc-id}/chunks/{chunk-id}  删除
    C5  PATCH  /knowledge-base/docs/{doc-id}/chunks/{chunk-id}/enable?value=  启用/禁用单条
    C6  PATCH  /knowledge-base/docs/{doc-id}/chunks/batch-enable?value=       批量启用/禁用

Controller 层只做「取服务 + 统一 Result 包裹 + camelize/VO 投影」薄转换（对齐 N2 document controller）。
VO 投影键核对自 Java KnowledgeChunkVO：id/kbId/docId/chunkIndex/content/contentHash/charCount/
tokenCount/enabled/createTime/updateTime（无 embeddingText、无 deleted）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.controller.KnowledgeChunkController
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from knowledge.controller.reqvo import (
    KnowledgeChunkBatchRequest,
    KnowledgeChunkCreateRequest,
    KnowledgeChunkUpdateRequest,
)
from rag.controller.vo import camelize

router = APIRouter(prefix="/knowledge-base/docs", tags=["knowledge-base-chunks"])

# Java KnowledgeChunkVO 投影键（核对自 KnowledgeChunkVO.java：无 embeddingText、无 deleted）
_CHUNK_VO_KEYS = (
    "id", "kb_id", "doc_id", "chunk_index", "content", "content_hash",
    "char_count", "token_count", "enabled", "create_time", "update_time",
)


def _container(request: Request) -> AppContainer:
    return request.app.state.container


def _project(row: dict) -> dict:
    """行 → VO 投影（仅 Java KnowledgeChunkVO 字段）"""
    return {k: row.get(k) for k in _CHUNK_VO_KEYS}


def _project_page(page: dict) -> dict:
    page["records"] = [_project(r) for r in page["records"]]
    return page


@router.get("/{doc_id}/chunks", name="page_chunks")
async def page_chunks(
    doc_id: str,
    request: Request,
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
    enabled: Optional[bool] = Query(default=None),
) -> dict:
    """C1：分页查询 Chunk（doc_id + enabled 可选过滤，chunk_index asc）"""
    container = _container(request)
    page = container.knowledge_chunk_service.page(
        doc_id, current=current, size=size, enabled=enabled
    )
    return result_to_dict(Results.success(camelize(_project_page(page))))


@router.post("/{doc_id}/chunks", name="create_chunk")
async def create_chunk(
    doc_id: str, request: Request, body: KnowledgeChunkCreateRequest
) -> dict:
    """C2：新增手工 Chunk（RUNNING/文档未启用拒；index 显式 or 自动）"""
    container = _container(request)
    row = await container.knowledge_chunk_service.create(
        doc_id, chunk_id=body.chunk_id, content=body.content, index=body.index
    )
    return result_to_dict(Results.success(camelize(_project(row))))


@router.put("/{doc_id}/chunks/{chunk_id}", name="update_chunk")
async def update_chunk(
    doc_id: str, chunk_id: str, request: Request, body: KnowledgeChunkUpdateRequest
) -> dict:
    """C3：更新 Chunk 内容（内容未变幂等 skip；向量文本随正文改）"""
    container = _container(request)
    await container.knowledge_chunk_service.update(
        doc_id, chunk_id, content=body.content
    )
    return result_to_dict(Results.success())


@router.delete("/{doc_id}/chunks/{chunk_id}", name="delete_chunk")
async def delete_chunk(doc_id: str, chunk_id: str, request: Request) -> dict:
    """C4：删除 Chunk（RUNNING 拒删；物理删 + 向量删）"""
    container = _container(request)
    await container.knowledge_chunk_service.delete(doc_id, chunk_id)
    return result_to_dict(Results.success())


@router.patch("/{doc_id}/chunks/{chunk_id}/enable", name="enable_chunk")
async def enable_chunk(
    doc_id: str, chunk_id: str, request: Request, value: bool = Query(...)
) -> dict:
    """C5：启用/禁用单条 Chunk（启用前文档 enabled 校验；状态未变幂等 skip）"""
    container = _container(request)
    await container.knowledge_chunk_service.enable_chunk(doc_id, chunk_id, value)
    return result_to_dict(Results.success())


@router.patch("/{doc_id}/chunks/batch-enable", name="batch_enable_chunks")
async def batch_enable_chunks(
    doc_id: str,
    request: Request,
    value: bool = Query(...),
    body: Optional[KnowledgeChunkBatchRequest] = None,
) -> dict:
    """C6：批量启用/禁用（≤500、全存在全归属、无变更拒；body 可缺省）"""
    container = _container(request)
    chunk_ids = body.chunk_ids if body is not None else None
    await container.knowledge_chunk_service.batch_toggle_enabled(
        doc_id, chunk_ids, value
    )
    return result_to_dict(Results.success())
