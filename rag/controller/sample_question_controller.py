# -*- coding: utf-8 -*-
"""
rag.controller.sample_question_controller - 示例问题 REST 端点（对应 Java SampleQuestionController）

示例问题域切片（C6，方案 B 重建）：
    - GET    /rag/sample-questions          随机示例问题列表（欢迎页展示，deleted=0 随机 3 条）
    - GET    /sample-questions              分页查询（current/size/keyword，MyBatis-Plus Page 语义）
    - GET    /sample-questions/{id}         详情
    - POST   /sample-questions              创建（返回新 ID）
    - PUT    /sample-questions/{id}         更新
    - DELETE /sample-questions/{id}         删除（软删）

方案 B：service 层返回 snake_case dict，本层经 pydantic `SampleQuestionVO`（alias camelCase，
`to_camel_dict` → model_dump(by_alias=True)）输出 camelCase JSON（createTime/updateTime），对齐 M2 风格。
统一 Result；ClientException（示例问题内容不能为空 / 示例问题不存在）由 D0.8 全局异常处理器转码。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.SampleQuestionController
    - com.nageoffer.ai.ragent.rag.controller.request.SampleQuestionCreateRequest / UpdateRequest / PageRequest
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.request import SampleQuestionCreateRequest, SampleQuestionUpdateRequest
from rag.controller.vo import SampleQuestionVO

router = APIRouter(tags=["sample-question"])


def _container(request: Request) -> AppContainer:
    """从应用状态取装配容器（service 经此注入）"""
    return request.app.state.container


def _vo(row: dict) -> dict:
    """service snake_case dict → camelCase VO dict"""
    return SampleQuestionVO.from_row(row).to_camel_dict()


# ==================== 随机示例问题（欢迎页） ====================


@router.get("/rag/sample-questions", name="list_random_sample_questions")
async def list_random_sample_questions(request: Request) -> dict:
    """GET /rag/sample-questions：随机示例问题列表（deleted=0 随机 3 条）"""
    container = _container(request)
    data = [_vo(r) for r in container.sample_question_service.list_random_questions()]
    return result_to_dict(Results.success(data))


# ==================== 管理 CRUD ====================


@router.get("/sample-questions", name="page_sample_questions")
async def page_sample_questions(
    request: Request,
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
    keyword: Optional[str] = Query(default=None),
) -> dict:
    """GET /sample-questions：分页查询示例问题（current 1 基，size<=0 防泄漏）"""
    container = _container(request)
    page = container.sample_question_service.page_query(
        current=current, size=size, keyword=keyword
    )
    page["records"] = [_vo(r) for r in page["records"]]
    return result_to_dict(Results.success(page))


@router.get("/sample-questions/{qid}", name="get_sample_question")
async def get_sample_question(qid: str, request: Request) -> dict:
    """GET /sample-questions/{id}：示例问题详情"""
    container = _container(request)
    return result_to_dict(
        Results.success(_vo(container.sample_question_service.query_by_id(qid)))
    )


@router.post("/sample-questions", name="create_sample_question")
async def create_sample_question(
    request: Request, payload: SampleQuestionCreateRequest
) -> dict:
    """POST /sample-questions：创建示例问题，返回新 ID"""
    container = _container(request)
    qid = container.sample_question_service.create(
        title=payload.title, description=payload.description, question=payload.question
    )
    return result_to_dict(Results.success(qid))


@router.put("/sample-questions/{qid}", name="update_sample_question")
async def update_sample_question(
    qid: str, request: Request, payload: SampleQuestionUpdateRequest
) -> dict:
    """PUT /sample-questions/{id}：更新示例问题（仅刷传非空字段）"""
    container = _container(request)
    container.sample_question_service.update(
        qid,
        title=payload.title,
        description=payload.description,
        question=payload.question,
    )
    return result_to_dict(Results.success(None))


@router.delete("/sample-questions/{qid}", name="delete_sample_question")
async def delete_sample_question(qid: str, request: Request) -> dict:
    """DELETE /sample-questions/{id}：软删示例问题"""
    container = _container(request)
    container.sample_question_service.delete(qid)
    return result_to_dict(Results.success(None))