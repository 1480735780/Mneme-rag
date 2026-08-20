# -*- coding: utf-8 -*-
"""
rag.controller.query_term_mapping_controller - 术语映射管理端点（对应 Java QueryTermMappingController，C8）

    - GET    /mappings        分页（current/size/keyword，priority asc + update_time desc）
    - GET    /mappings/{id}   详情
    - POST   /mappings        创建（返回新 ID）
    - PUT    /mappings/{id}   更新（仅传需更新字段）
    - DELETE /mappings/{id}   删除（物理删）

方案 B：service 返回 snake_case dict，边界经 `camelize` 转 camelCase。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.QueryTermMappingController
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.request import QueryTermMappingCreateRequest, QueryTermMappingUpdateRequest
from rag.controller.vo import camelize

router = APIRouter(prefix="/mappings", tags=["term-mapping"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("", name="page_term_mappings")
async def page_term_mappings(
    request: Request,
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
    keyword: Optional[str] = Query(default=None),
) -> dict:
    container = _container(request)
    page = container.query_term_mapping_admin_service.page_query(
        current=current, size=size, keyword=keyword
    )
    return result_to_dict(Results.success(camelize(page)))


@router.get("/{mid}", name="get_term_mapping")
async def get_term_mapping(mid: str, request: Request) -> dict:
    container = _container(request)
    return result_to_dict(
        Results.success(camelize(container.query_term_mapping_admin_service.query_by_id(mid)))
    )


@router.post("", name="create_term_mapping")
async def create_term_mapping(request: Request, payload: QueryTermMappingCreateRequest) -> dict:
    container = _container(request)
    mid = container.query_term_mapping_admin_service.create(**payload.model_dump())
    return result_to_dict(Results.success(mid))


@router.put("/{mid}", name="update_term_mapping")
async def update_term_mapping(
    mid: str, request: Request, payload: QueryTermMappingUpdateRequest
) -> dict:
    container = _container(request)
    container.query_term_mapping_admin_service.update(mid, **payload.model_dump())
    return result_to_dict(Results.success(None))


@router.delete("/{mid}", name="delete_term_mapping")
async def delete_term_mapping(mid: str, request: Request) -> dict:
    container = _container(request)
    container.query_term_mapping_admin_service.delete(mid)
    return result_to_dict(Results.success(None))