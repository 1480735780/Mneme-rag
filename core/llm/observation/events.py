# -*- coding: utf-8 -*-
"""
core.llm.observation.events - LLM 调用观测事件模型（P10.1，决策 D3/D4）

一次真实 API 调用（含 transient 重试与 TTFT 切换）= 一条 CallEvent = 一行 t_llm_call。
rerank 短路（候选 ≤ topN 不调 API）不产生事件；canceled 不进失败率但 token 照常固化
成本（断开前的消耗是真金白银，§6 R2-Q11）。

类型/场景/目的/状态/来源字面量全部集中在本模块（CHUNK_ROLE 先例：散落字面量会
静默筛不到行）；新调用点在 PURPOSE_* 登记，不各自造词。

对应 P10 计划：docs/rag/p10-observability-operations-implementation-plan.md §2 D3/D4
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Optional

# ===================== 调用类型（D3：四类全采） =====================

CALL_KIND_CHAT = "chat"
CALL_KIND_EMBEDDING = "embedding"
CALL_KIND_RERANK = "rerank"
CALL_KIND_VLM = "vlm"
CALL_KINDS = (CALL_KIND_CHAT, CALL_KIND_EMBEDDING, CALL_KIND_RERANK, CALL_KIND_VLM)

# ===================== 场景（D3：成本归因维度，后台任务靠它而非 run_id 空值） =====================

SCENE_CHAT = "chat"
SCENE_KB_BUILD = "kb_build"
SCENE_EVAL = "eval"
SCENE_SMOKE_TEST = "smoke_test"
SCENE_UNKNOWN = "unknown"  # scope 未设置的兜底——unknown 占比是观测层自身健康指标（D5）
SCENES = (SCENE_CHAT, SCENE_KB_BUILD, SCENE_EVAL, SCENE_SMOKE_TEST, SCENE_UNKNOWN)

# ===================== 调用目的（D3 purpose：调用点标注，成本归因到用途） =====================

PURPOSE_UNKNOWN = "unknown"
PURPOSE_REWRITE = "rewrite"          # rag/rewrite/query_rewrite.py
PURPOSE_INTENTS = "intents"          # rag/intent/classifier.py（含意图叶子向量化）
PURPOSE_GUIDANCE = "guidance"        # rag/guidance/checker.py
PURPOSE_GENERATE = "generate"        # rag/engine.py（含 stream 生成）
PURPOSE_TITLE = "title"              # rag/service/conversation_service.py
PURPOSE_RECOMMEND = "recommend"      # rag/service/recommended_question_service.py
PURPOSE_SUMMARY = "summary"          # rag/memory/summary.py
PURPOSE_MCP_EXTRACT = "mcp_extract"  # rag/mcp/llm_extractor.py
PURPOSE_ENRICH = "enrich"            # ingestion/node/enricher_node.py
PURPOSE_ENHANCE = "enhance"          # ingestion/node/enhancer_node.py
PURPOSE_REEMBED = "reembed"          # knowledge/service/chunk.py 重嵌入
PURPOSE_VLM_DESCRIBE = "vlm_describe"  # rag/ingestion/parser 图片/解析图生文

# ===================== 调用状态（D4 三级口径：canceled 不进失败率但计成本） =====================

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"
STATUSES = (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED)

# ===================== token 来源（D6：估算覆盖率一等公民，>30% 出 P2） =====================

TOKEN_SOURCE_REAL = "real"
TOKEN_SOURCE_ESTIMATED = "estimated"
TOKEN_SOURCE_MIXED = "mixed"


@dataclass(frozen=True)
class BreakerEvent:
    """
    熔断器状态变更事件（P10.1 T1.7，D12：schema 随接缝冻结——T4.2 告警消费者
    照此写码，P10.1 落地前即可开发）。

    Attributes:
        model_id: 模型唯一标识（熔断器粒度）。
        from_state / to_state: 转换前后状态（closed / open / half_open）。
        failure_count: 触发时刻的失败统计快照（阈值路径 = 触发计数；探测路径 = 0）。
        create_time: 事件时间（发射点物化）。
    """

    model_id: str
    from_state: str
    to_state: str
    failure_count: int
    create_time: str


@dataclass(frozen=True)
class CallEvent:
    """
    一次真实 LLM API 调用的观测事件（发射点物化 → 队列 → flush 落库 t_llm_call）

    D4/D5 两条硬纪律在本模型的体现：
        - request_uid 无默认值：逻辑调用分组键必填（空 trace 请求级指标的 SQL 归组依据）；
        - scene/purpose 不允许 None：未设置即 unknown，漏埋点在看板上自行暴露。
    """

    request_uid: str
    call_kind: str
    provider: str
    model: str
    status: str
    scene: str = SCENE_UNKNOWN
    purpose: str = PURPOSE_UNKNOWN
    tier: Optional[str] = None
    trace_id: Optional[str] = None
    user_id: Optional[str] = None
    attempt_no: int = 1
    is_final: bool = False
    error_type: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    token_source: Optional[str] = None
    latency_ms: Optional[int] = None
    ttft_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    pricing_snapshot: Optional[Dict] = None
    ext: Optional[Dict] = None
    create_time: Optional[str] = None  # 事件时间（发射点物化）；缺省由 DAO 兜底为落库时间

    def to_row(self) -> Dict:
        """事件 → t_llm_call 行（JSONB 列走 json.dumps 字符串，对齐 t_rag_trace_run.extra_data 先例）"""
        return {
            "call_kind": self.call_kind,
            "scene": self.scene,
            "purpose": self.purpose,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "request_uid": self.request_uid,
            "attempt_no": self.attempt_no,
            "is_final": 1 if self.is_final else 0,
            "status": self.status,
            "error_type": self.error_type,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "token_source": self.token_source,
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "cost_usd": self.cost_usd,
            "pricing_snapshot": (
                json.dumps(self.pricing_snapshot, ensure_ascii=False)
                if self.pricing_snapshot is not None
                else None
            ),
            "ext": (
                json.dumps(self.ext, ensure_ascii=False) if self.ext is not None else None
            ),
            "create_time": self.create_time,
        }
