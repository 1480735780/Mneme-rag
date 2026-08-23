# -*- coding: utf-8 -*-
"""
ingestion.controller.reqvo - 摄取请求模型（对应 Java ingestion/controller/request/*）

字段名即 Java camelCase，pydantic 原生按 JSON 键匹配（无需 alias）。

对应 ragent 源码：
    - ingestion/controller/request/IngestionPipeline{Create,Update}Request
    - ingestion/controller/request/IngestionPipelineNodeRequest
    - ingestion/controller/request/IngestionTaskCreateRequest
    - rag/controller/request/DocumentSourceRequest
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class IngestionPipelineNodeRequest(BaseModel):
    """流水线节点请求（对应 Java IngestionPipelineNodeRequest）"""

    nodeId: str
    nodeType: str
    nextNodeId: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    condition: Optional[Dict[str, Any]] = None


class IngestionPipelineCreateRequest(BaseModel):
    """创建流水线请求（对应 Java IngestionPipelineCreateRequest）"""

    name: str
    description: Optional[str] = None
    nodes: List[IngestionPipelineNodeRequest] = Field(default_factory=list)


class IngestionPipelineUpdateRequest(BaseModel):
    """更新流水线请求（对应 Java IngestionPipelineUpdateRequest；无 enabled 语义）"""

    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[List[IngestionPipelineNodeRequest]] = None


class DocumentSourceRequest(BaseModel):
    """文档源请求（对应 Java DocumentSourceRequest）"""

    type: str
    location: Optional[str] = None
    fileName: Optional[str] = None
    credentials: Optional[Dict[str, str]] = None


class IngestionTaskCreateRequest(BaseModel):
    """创建任务请求（对应 Java IngestionTaskCreateRequest）"""

    pipelineId: str
    source: DocumentSourceRequest
    metadata: Optional[Dict[str, Any]] = None
    vectorSpaceId: Optional[Dict[str, Any]] = None
