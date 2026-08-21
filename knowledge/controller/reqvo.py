# -*- coding: utf-8 -*-
"""
knowledge.controller.reqvo - 知识库请求整合模型（对应 Java KnowledgeBaseCreate/UpdateRequest）

方案 B：HTTP 入参用 camelCase alias（对齐 Java 字段名 embeddingModel/collectionName），
pydantic 以 snake_case 属性承载；service 层不经此模型、感知 dict/行。

对应 ragent 源码：
    - knowledge/controller/request/KnowledgeBaseCreateRequest
    - knowledge/controller/request/KnowledgeBaseUpdateRequest
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求（对齐 Java KnowledgeBaseCreateRequest：name/embeddingModel/collectionName）"""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    embedding_model: Optional[str] = Field(default=None, alias="embeddingModel")
    collection_name: str = Field(alias="collectionName")


class KnowledgeBaseUpdateRequest(BaseModel):
    """更新/重命名知识库请求（对齐 Java KnowledgeBaseUpdateRequest：id/name/embeddingModel）"""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    name: Optional[str] = None
    embedding_model: Optional[str] = Field(default=None, alias="embeddingModel")


class KnowledgeDocumentUpdateRequest(BaseModel):
    """更新文档请求（对齐 Java KnowledgeDocumentUpdateRequest；docName/processMode 等 camelCase）"""

    model_config = ConfigDict(populate_by_name=True)

    doc_name: Optional[str] = Field(default=None, alias="docName")
    process_mode: Optional[str] = Field(default=None, alias="processMode")
    ingestion_spec: Optional[str] = Field(default=None, alias="ingestionSpec")
    pipeline_id: Optional[str] = Field(default=None, alias="pipelineId")
    source_location: Optional[str] = Field(default=None, alias="sourceLocation")
    schedule_enabled: Optional[int] = Field(default=None, alias="scheduleEnabled")
    schedule_cron: Optional[str] = Field(default=None, alias="scheduleCron")