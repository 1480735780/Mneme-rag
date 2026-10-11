# -*- coding: utf-8 -*-
"""
core.llm.observation - LLM 调用观测（P10.1）

发射点（routing_executor / chat.py，T1.3/T1.4 接线）把 CallEvent 投入
ObservationQueue，后台批量 flush 落库 t_llm_call；上下文传播遵循 D5
（CallScope：core 定义、rag/ingestion 设置、发射点物化、unknown 兜底）。

观测不耦合推理路径（D2）：put_nowait 满即丢，双阈值批量 flush，退出 drain，
一切丢失经 ObservationStats 可观测。
"""
from core.llm.observation.collector import ObservationStats
from core.llm.observation.dao import LLM_CALL_TABLE, LlmCallDao
from core.llm.observation.hub import ObservationHub
from core.llm.observation.events import (
    CALL_KINDS,
    CALL_KIND_CHAT,
    CALL_KIND_EMBEDDING,
    CALL_KIND_RERANK,
    CALL_KIND_VLM,
    PURPOSE_UNKNOWN,
    PURPOSE_ENHANCE,
    PURPOSE_ENRICH,
    PURPOSE_GENERATE,
    PURPOSE_GUIDANCE,
    PURPOSE_INTENTS,
    PURPOSE_MCP_EXTRACT,
    PURPOSE_RECOMMEND,
    PURPOSE_REEMBED,
    PURPOSE_REWRITE,
    PURPOSE_SUMMARY,
    PURPOSE_TITLE,
    PURPOSE_VLM_DESCRIBE,
    SCENES,
    SCENE_CHAT,
    SCENE_EVAL,
    SCENE_KB_BUILD,
    SCENE_SMOKE_TEST,
    SCENE_UNKNOWN,
    STATUSES,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TOKEN_SOURCE_ESTIMATED,
    TOKEN_SOURCE_MIXED,
    TOKEN_SOURCE_REAL,
    CallEvent,
)
from core.llm.observation.queue import ObservationQueue
from core.llm.observation.scope import CallScope, current_scope, llm_call_scope

__all__ = [
    "ObservationStats",
    "LLM_CALL_TABLE",
    "LlmCallDao",
    "CallEvent",
    "CALL_KINDS", "CALL_KIND_CHAT", "CALL_KIND_EMBEDDING", "CALL_KIND_RERANK", "CALL_KIND_VLM",
    "SCENES", "SCENE_CHAT", "SCENE_EVAL", "SCENE_KB_BUILD", "SCENE_SMOKE_TEST", "SCENE_UNKNOWN",
    "PURPOSE_UNKNOWN", "PURPOSE_REWRITE", "PURPOSE_INTENTS", "PURPOSE_GUIDANCE",
    "PURPOSE_GENERATE", "PURPOSE_TITLE", "PURPOSE_RECOMMEND", "PURPOSE_SUMMARY",
    "PURPOSE_MCP_EXTRACT", "PURPOSE_ENRICH", "PURPOSE_ENHANCE", "PURPOSE_REEMBED",
    "PURPOSE_VLM_DESCRIBE",
    "STATUSES", "STATUS_SUCCESS", "STATUS_FAILED", "STATUS_CANCELED",
    "TOKEN_SOURCE_REAL", "TOKEN_SOURCE_ESTIMATED", "TOKEN_SOURCE_MIXED",
    "ObservationQueue",
    "ObservationHub",
    "CallScope", "current_scope", "llm_call_scope",
]
