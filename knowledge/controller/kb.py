# -*- coding: utf-8 -*-
"""
knowledge.controller.kb - 知识库端点（对应 Java KnowledgeBaseController，K1-K5）

    - POST   /knowledge-base                   创建（返回 id）
    - PUT    /knowledge-base/{kb-id}           重命名
    - DELETE /knowledge-base/{kb-id}           删除
    - GET    /knowledge-base/{kb-id}           详情
    - GET    /knowledge-base                   分页列表（name like + doc_count 聚合）

Controller 层只做「取服务 + 统一 Result 包裹 + camelize」薄转换；service 返回 snake_case dict，
边界经 camelize 递归转 camelCase（对齐 Java VO，如 embeddingModel/collectionName/documentCount）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.controller.KnowledgeBaseController
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from knowledge.controller.reqvo import KnowledgeBaseCreateRequest, KnowledgeBaseUpdateRequest
from rag.controller.vo import camelize

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

# Java KnowledgeBaseVO 投影键（排 deleted/updatedBy 等内部列；document_count 仅分页有）
_KB_VO_KEYS = ("id", "name", "embedding_model", "collection_name", "created_by", "create_time", "update_time")


def _project(row: dict) -> dict:
    """行 → VO 投影（对齐 Java BeanUtil.toBean 消费子集映射）"""
    vo = {k: row.get(k) for k in _KB_VO_KEYS}
    if "document_count" in row:
        vo["document_count"] = row["document_count"]
    return vo


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.post("", name="create_knowledge_base", status_code=200)
async def create_knowledge_base(request: Request, body: KnowledgeBaseCreateRequest) -> dict:
    """POST /knowledge-base：创建知识库（返回新 id）"""
    container = _container(request)
    kb_id = container.knowledge_base_service.create(
        body.name, body.embedding_model, body.collection_name
    )
    return result_to_dict(Results.success(camelize(kb_id)))


@router.put("/{kb_id}", name="rename_knowledge_base")
async def rename_knowledge_base(kb_id: str, request: Request, body: KnowledgeBaseUpdateRequest) -> dict:
    """PUT /knowledge-base/{kb-id}：重命名知识库"""
    container = _container(request)
    container.knowledge_base_service.rename(kb_id, body.name)
    return result_to_dict(Results.success())


@router.delete("/{kb_id}", name="delete_knowledge_base")
async def delete_knowledge_base(kb_id: str, request: Request) -> dict:
    """DELETE /knowledge-base/{kb-id}：删除知识库（有未删文档拒绝）"""
    container = _container(request)
    # 删除前取 collection（软删后不可查）；删除后 best-effort 清图谱（RAGENT_RETRIEVAL_GRAPH=on 时注入）
    kb = container.knowledge_base_service.query_by_id(kb_id)
    container.knowledge_base_service.delete(kb_id)
    graph_cleaner = container.knowledge_base_service.graph_cleaner
    if graph_cleaner is not None:
        await graph_cleaner(kb["collection_name"])
    return result_to_dict(Results.success())


@router.get("/{kb_id}", name="query_knowledge_base")
async def query_knowledge_base(kb_id: str, request: Request) -> dict:
    """GET /knowledge-base/{kb-id}：知识库详情（不存在 → ClientException → Result error）"""
    container = _container(request)
    row = container.knowledge_base_service.query_by_id(kb_id)
    return result_to_dict(Results.success(camelize(_project(row))))


@router.get("", name="page_knowledge_base")
async def page_knowledge_base(
    request: Request,
    name: Optional[str] = Query(default=None),
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
) -> dict:
    """GET /knowledge-base：分页列表（name like + update_time desc + 每库 document_count）"""
    container = _container(request)
    page = container.knowledge_base_service.page_query(current=current, size=size, keyword=name)
    page["records"] = [_project(r) for r in page["records"]]
    return result_to_dict(Results.success(camelize(page)))