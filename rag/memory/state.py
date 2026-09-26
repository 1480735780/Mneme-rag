# -*- coding: utf-8 -*-
"""
rag.memory.state - 会话压缩结构化状态（Mneme-rag V2.1.1 / P-01）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 1 节 P-01

设计要点：
    1. ConversationState 只保留 **4 个 slot**（不含 references）：
        user_goal / progress / open_questions / critical_context
       references 由 Evidence 派生（见 rag.memory.evidence），LLM 摘要不产此字段，
       避免"schema 与 Evidence 表双写、LLM 幻觉 doc_id"。
    2. LLM 输出走"宽松解析 + 兜底"三段：
        严格 JSON → 提取 ```json``` 代码块 → 提取 { ... } 最外层
       三段全失败返回 None，由调用方决定"保留旧 state 不覆盖"（V2.1.1 P-08 会在此
       基础上扩展三级自愈；本文件仅提供最基础的解析语义，不做 provider 分支）。
    3. 渲染：把 ConversationState + 派生的 evidence refs 组装成一段自然文本，
       作为 SYSTEM 消息内容注入 Prompt（对齐 V1 summary-wrapper 消费方接口）。

兼容性：
    - summary_version='v1' 或为空 → 上层走 V1 路径直接返回原文本，不进入本模块
    - summary_version='v2' → 使用 structured_content JSON 反序列化为 ConversationState

对应 Java 侧无（新概念，源自 pi-mono / V2.1 设计文档）
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field, ValidationError

# 摘要 schema 版本，未来 slot 调整时递增（v2 / v2.1 / v3 ...）
SCHEMA_VERSION = "v2"

# 从 LLM 输出中提取 fenced json 代码块（```json ... ``` 或 ``` ... ```）
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# 从自由文本中提取最外层 JSON 对象（贪婪匹配到最后一个 } 会跨越其他字段，用非贪婪+平衡兜底）
_OUTER_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class EvidenceRef(BaseModel):
    """
    知识引用（装配阶段派生注入，LLM 摘要不产）

    字段来源（V2.1.1 P-01 阶段实现）：
        doc_id / doc_name 从 t_message.sources JSONB 列（SourceRef 结构）派生
        chunk_id 暂缺（Phase 7 Evidence Ledger 建表后补齐）
        reason 暂空（V2.1.1 P-01 阶段不做 LLM 生成 reason，装配时留占位）
        score 从 SourceRef.index 归一化（index=1 → 高分），仅作排序 tie-breaker
    """

    doc_id: str
    doc_name: Optional[str] = None
    chunk_id: Optional[str] = None
    reason: Optional[str] = None
    score: Optional[float] = None

    # 序列化统一走 dict，避免 pydantic v2 model_dump_json 输出带 pydantic 私货
    def to_json_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class ConversationState(BaseModel):
    """
    会话压缩结构化状态（V2.1.1 P-01：4 slot，不含 references）

    Attributes:
        user_goal: 用户当前想完成的事（单值，允许改写；话题延续时重写为覆盖两者的完整表述）
        progress: 已完成的里程碑列表（append-only，不重写旧项措辞；被后续消息否定的项可删除）
        open_questions: 未解决的追问（**每轮重算**，被后续消息回答的直接删除，允许输出比上一轮更短）
        critical_context: 关键上下文（约束/偏好/实体定义/参数值，**去重合并**，新旧并存）
    """

    user_goal: str = ""
    progress: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    critical_context: List[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """所有 slot 都为空/默认值时视为"未产生有效摘要"，调用方可选择保留旧值"""
        return (
            not (self.user_goal or "").strip()
            and not self.progress
            and not self.open_questions
            and not self.critical_context
        )

    def to_json_dict(self) -> dict:
        """序列化为可 json.dumps 的 dict（不落 None，保留空 list 以便前端展示占位）"""
        return {
            "user_goal": self.user_goal or "",
            "progress": list(self.progress or []),
            "open_questions": list(self.open_questions or []),
            "critical_context": list(self.critical_context or []),
        }


def parse_state_from_llm_output(text: Optional[str]) -> Optional[ConversationState]:
    """
    三段宽松解析 LLM 输出为 ConversationState：
        1. 严格 json.loads
        2. 从 ```json ... ``` 代码块提取
        3. 从自由文本中提取最外层 {...}
    全失败返回 None。Pydantic 校验失败（字段类型错）也返回 None（视为解析失败）。

    Args:
        text: LLM 原始输出

    Returns:
        Optional[ConversationState]: 解析成功则返回，失败返回 None
    """
    if not text or not text.strip():
        return None

    for candidate in _iter_json_candidates(text):
        state = _try_validate(candidate)
        if state is not None:
            return state
    return None


def _iter_json_candidates(text: str):
    """按优先级 yield 可能的 JSON 字符串候选"""
    stripped = text.strip()
    yield stripped

    m = _FENCED_JSON_RE.search(text)
    if m:
        yield m.group(1).strip()

    m = _OUTER_JSON_RE.search(text)
    if m:
        yield m.group(0).strip()


def _try_validate(candidate: str) -> Optional[ConversationState]:
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return ConversationState.model_validate(obj)
    except ValidationError:
        return None


def render_state_to_prompt_text(
    state: ConversationState,
    evidence_refs: Optional[List[EvidenceRef]] = None,
) -> str:
    """
    把 ConversationState + 派生 evidence refs 渲染成一段自然文本，
    作为 summary-wrapper 的 content 内容注入到 SYSTEM 消息。

    渲染格式（面向 LLM 阅读，非面向用户 UI）：

        <conversation-state>
        [Goal] ...
        [Progress]
        - ...
        [Open Questions]
        - ...
        [Critical Context]
        - ...
        </conversation-state>

        <evidence-refs>
        - doc=<doc_id> name=<doc_name>
        ...
        </evidence-refs>

    空 slot 输出为 `[Section] (none)` 保持结构稳定，避免 LLM 因缺失段头误以为格式漂移。
    """
    parts: List[str] = ["<conversation-state>"]

    parts.append(f"[Goal] {_safe(state.user_goal) or '(none)'}")
    parts.append("[Progress]")
    parts.append(_bullets(state.progress))
    parts.append("[Open Questions]")
    parts.append(_bullets(state.open_questions))
    parts.append("[Critical Context]")
    parts.append(_bullets(state.critical_context))
    parts.append("</conversation-state>")

    if evidence_refs:
        parts.append("")
        parts.append("<evidence-refs>")
        for ref in evidence_refs:
            parts.append(_render_evidence_line(ref))
        parts.append("</evidence-refs>")

    # 清理 (none) 与真实条目之间可能出现的多余空行
    return _collapse_blank_lines("\n".join(parts))


def render_state_to_content_snapshot(
    state: ConversationState,
    evidence_refs: Optional[List[EvidenceRef]] = None,
) -> str:
    """
    为兼容 V1 消费路径写入 t_conversation_summary.content 列的文本快照。
    语义等同 render_state_to_prompt_text，但**不含 evidence refs**——
    V1 路径读到的是"写入时刻的 state"，避免"content 里嵌入的 refs 已过时但仍被读到"。
    装配阶段（load_latest_summary）走 render_state_to_prompt_text 动态合并最新 refs。
    """
    return render_state_to_prompt_text(state, evidence_refs=None)


def _safe(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bullets(items: List[str]) -> str:
    cleaned = [_safe(i) for i in (items or []) if _safe(i)]
    if not cleaned:
        return "(none)"
    return "\n".join(f"- {line}" for line in cleaned)


def _render_evidence_line(ref: EvidenceRef) -> str:
    """
    单行 evidence：`- doc=<id> name=<name> [reason=<reason>]`
    score 只用于排序不落到文本，避免模型对数值产生额外解读。
    """
    bits = [f"doc={ref.doc_id}"]
    if ref.doc_name:
        bits.append(f"name={ref.doc_name}")
    if ref.chunk_id:
        bits.append(f"chunk={ref.chunk_id}")
    if ref.reason:
        bits.append(f"reason={ref.reason}")
    return "- " + " ".join(bits)


def _collapse_blank_lines(text: str) -> str:
    """合并连续空行至最多一个（保持段间呼吸感但不留大空洞）"""
    out: List[str] = []
    prev_blank = False
    for line in text.splitlines():
        if not line.strip():
            if not prev_blank:
                out.append("")
            prev_blank = True
        else:
            out.append(line)
            prev_blank = False
    return "\n".join(out).strip()
