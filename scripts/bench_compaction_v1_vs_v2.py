# -*- coding: utf-8 -*-
"""
scripts/bench_compaction_v1_vs_v2.py — V1 vs V2 上下文压缩真 LLM 对比基准 v2

**本轮改进**（v2）：
    1. 多轮压缩：每个 case 3 轮，测完最后一轮的 retention（暴露 V1 telephone game）
    2. 预算对齐：V1 与 V2 都用 summary_max_chars=500（V2 结构标签开销 ~120 字，
       这样双方"真实内容预算"接近公平）
    3. 槽位感知：V2 probe 除全字符串 loose 命中外，额外检查是否落在 expected_slot
       （通常是 critical_context），报告展示 loose/strict 双分数

**评测轴**（每 case）：
    V1_loose / V2_loose    = 关键词出现在最终输出的字符串包含判定
    V2_strict              = 关键词真落在期望 slot 的严格判定（只算 expected_slot 非 None 的）
    tokens                 = 最终摘要字符数 / 4
    latency                = 3 轮端到端毫秒累计
    V2_written / V2_blocked= 3 轮里成功写行数 / merge 拦截轮数

**运行**：
    python scripts/bench_compaction_v1_vs_v2.py                        # 默认 7b
    python scripts/bench_compaction_v1_vs_v2.py --ollama-model qwen2.5:3b
    python scripts/bench_compaction_v1_vs_v2.py --limit 1 --quiet
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("bench")

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


# ==================== 真 LLM 客户端（Ollama OpenAI 兼容端点） ====================


class OllamaChatClient:
    def __init__(self, base_url: str, model: str, timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.call_count = 0

    async def chat(self, request, tier=None) -> str:  # noqa: ANN001
        self.call_count += 1
        payload = {
            "model": self.model,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in request.messages
            ],
            "temperature": float(getattr(request, "temperature", 0.3) or 0.3),
            "top_p": float(getattr(request, "topP", 0.9) or 0.9),
            "stream": False,
            "options": {"num_ctx": 8192},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as ex:
            raise RuntimeError(f"Ollama 调用失败: {ex}") from ex
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as ex:
            raise RuntimeError(f"Ollama 响应结构异常: {data}") from ex

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, len(text or "") // 4)


# ==================== V1 路径：自由文本摘要模拟器 ====================

_V1_SYSTEM_PROMPT_TEMPLATE = (
    "你是会话摘要助手。请将以下历史对话压缩为不超过 {max_chars} 字的摘要，"
    "保留关键事实、已解决的问题与尚未解决的疑问，使用中文输出。"
)


class V1CompactionSimulator:
    """
    模拟 P-01 之前 mneme-rag V1 摘要流水线：输入本轮扁平化对话文本，
    与已有自由文本摘要合并 → LLM → 新的自由文本。
    """

    def __init__(self, llm: OllamaChatClient, summary_max_chars: int = 500):
        self._llm = llm
        self._max_chars = summary_max_chars
        self._current_summary: str = ""

    async def compress_once(self, messages_text: str) -> str:
        from core.llm.enums import Tier
        from core.llm.schema import ChatRequest, Message

        msg_list = [
            Message.system(_V1_SYSTEM_PROMPT_TEMPLATE.format(max_chars=self._max_chars))
        ]
        if self._current_summary:
            msg_list.append(
                Message.assistant("历史摘要（用于合并去重）：\n" + self._current_summary)
            )
        msg_list.append(Message.user("本轮对话：\n" + messages_text))
        msg_list.append(
            Message.user(
                f"合并以上对话与历史摘要，去重后输出更新摘要。要求：严格≤"
                f"{self._max_chars}字符；仅一行。"
            )
        )
        request = ChatRequest(
            messages=msg_list, temperature=0.3, topP=0.9, thinking=False
        )
        try:
            new_summary = await self._llm.chat(request, tier=Tier.FAST)
            if new_summary and new_summary.strip():
                self._current_summary = new_summary.strip()
        except Exception as ex:  # noqa: BLE001
            logger.warning("V1 摘要 LLM 调用失败: %s", ex)
        return self._current_summary

    def load_context(self) -> str:
        return self._current_summary


# ==================== V2 路径：真 service ====================


def _build_v2_service(llm: OllamaChatClient):
    from rag.memory.config import MemoryProperties
    from rag.prompt.builder import (
        DEFAULT_AGENT_PROMPTS,
        AgentPromptSlot,
        StaticAgentPromptResolver,
    )
    from rag.memory.summary import DatabaseConversationMemorySummaryService
    from storage.database import InMemoryDatabaseClient

    db = InMemoryDatabaseClient(
        {"t_message": [], "t_conversation_summary": [], "t_conversation": []}
    )
    resolver = StaticAgentPromptResolver(
        prompts={
            AgentPromptSlot.CONVERSATION_SUMMARY.name:
                DEFAULT_AGENT_PROMPTS[AgentPromptSlot.CONVERSATION_SUMMARY.name]
        }
    )
    props = MemoryProperties(
        summary_enabled=True,
        summary_start_turns=1,
        history_keep_turns=4,   # 多轮场景：窗口 4 让每轮都可能触发压缩
        summary_max_chars=500,  # V2 结构开销 ~120 字，500 与 V1 内容预算对齐
    )
    service = DatabaseConversationMemorySummaryService(
        db=db,
        llm_service=llm,  # type: ignore[arg-type]
        prompt_resolver=resolver,
        properties=props,
    )
    return service, db


# ==================== Bench Case 定义 ====================


@dataclass
class Probe:
    text: str
    expected_slot: Optional[str] = None  # V2 严格判定期望 slot


@dataclass
class BenchCase:
    cid: str
    category: str
    title: str
    rounds: List[List[Tuple[str, str]]]
    probes: List[Probe]


def _make_cases() -> List[BenchCase]:
    cases: List[BenchCase] = []

    # ---- Correction ----
    cases.append(BenchCase(
        cid="C1", category="Correction", title="DB 从 MySQL 改为 PostgreSQL",
        rounds=[
            [("user", "开始选型：DB_ENGINE=MYSQL_X"), ("assistant", "记录，先默认 MySQL")],
            [("user", "刚才 DB 不对，改成 DB_ENGINE=POSTGRESQL_Y"), ("assistant", "OK 更新")],
            [("user", "聊聊日志格式"), ("assistant", "json"),
             ("user", "监控端口"), ("assistant", "9090")],
        ],
        probes=[Probe("POSTGRESQL_Y", "critical_context")],
    ))
    cases.append(BenchCase(
        cid="C2", category="Correction", title="Python 版本从 3.10 升到 3.13",
        rounds=[
            [("user", "环境配置 PY_VER=3.10A"), ("assistant", "已配")],
            [("user", "PY_VER 改成 3.13B"), ("assistant", "OK")],
            [("user", "代码风格"), ("assistant", "black"),
             ("user", "commit 规范"), ("assistant", "conventional")],
        ],
        probes=[Probe("3.13B", "critical_context")],
    ))

    # ---- Interference ----
    cases.append(BenchCase(
        cid="I1", category="Interference", title="关键事实后接 6 轮闲聊",
        rounds=[
            [("user", "记一下 BACKEND_STACK=FastAPI_Z"), ("assistant", "OK")],
            [("user", "今天天气"), ("assistant", "晴"),
             ("user", "推荐部电影"), ("assistant", "科幻")],
            [("user", "午饭"), ("assistant", "面"),
             ("user", "咖啡"), ("assistant", "美式"),
             ("user", "鞋子"), ("assistant", "亚瑟士"),
             ("user", "周末"), ("assistant", "爬山")],
        ],
        probes=[Probe("FastAPI_Z", "critical_context")],
    ))
    cases.append(BenchCase(
        cid="I2", category="Interference", title="参数值被 8 轮闲聊夹击",
        rounds=[
            [("user", "RETRY_K=7 次"), ("assistant", "记下了")],
            [("user", "话题 0"), ("assistant", "回 0"),
             ("user", "话题 1"), ("assistant", "回 1")],
            [("user", "话题 2"), ("assistant", "回 2"),
             ("user", "话题 3"), ("assistant", "回 3"),
             ("user", "话题 4"), ("assistant", "回 4"),
             ("user", "话题 5"), ("assistant", "回 5")],
        ],
        probes=[Probe("RETRY_K=7", "critical_context")],
    ))

    # ---- Multi-hop ----
    cases.append(BenchCase(
        cid="M1", category="Multi-hop", title="ALPHA_CORE 值 + 应用于服务 A",
        rounds=[
            [("user", "定义 ALPHA_CORE=value_Q"), ("assistant", "记下")],
            [("user", "服务 A 用 ALPHA_CORE 作为主键"), ("assistant", "OK")],
            [("user", "讨论日志"), ("assistant", "json"),
             ("user", "讨论部署"), ("assistant", "k8s")],
        ],
        probes=[
            Probe("ALPHA_CORE", "critical_context"),
            Probe("value_Q", "critical_context"),
        ],
    ))
    cases.append(BenchCase(
        cid="M2", category="Multi-hop", title="X_TIMEOUT=42 被网关引用",
        rounds=[
            [("user", "配置 X_TIMEOUT=42"), ("assistant", "记下了")],
            [("user", "网关请求超时用 X_TIMEOUT"), ("assistant", "OK")],
            [("user", "缓存策略"), ("assistant", "ttl 300"),
             ("user", "压缩算法"), ("assistant", "zstd")],
        ],
        probes=[
            Probe("X_TIMEOUT", "critical_context"),
            Probe("42", "critical_context"),
        ],
    ))

    # ---- Rename ----
    cases.append(BenchCase(
        cid="R1", category="Rename", title="KnowledgeDocument 简称 KD_DOC_ALIAS",
        rounds=[
            [("user", "以后 KnowledgeDocument 简称 KD_DOC_ALIAS"), ("assistant", "好的")],
            [("user", "讨论 chunking"), ("assistant", "按标题切"),
             ("user", "讨论 embedding"), ("assistant", "BGE-M3")],
            [("user", "讨论 rerank"), ("assistant", "bge-reranker"),
             ("user", "讨论索引"), ("assistant", "HNSW")],
        ],
        probes=[Probe("KD_DOC_ALIAS", "critical_context")],
    ))
    cases.append(BenchCase(
        cid="R2", category="Rename", title="项目 codename MNEME_PROJECT_X",
        rounds=[
            [("user", "项目 codename 是 MNEME_PROJECT_X"), ("assistant", "记下")],
            [("user", "讨论日志"), ("assistant", "structured"),
             ("user", "讨论 metrics"), ("assistant", "prometheus")],
            [("user", "讨论 tracing"), ("assistant", "otel"),
             ("user", "讨论部署"), ("assistant", "k8s")],
        ],
        probes=[Probe("MNEME_PROJECT_X", "critical_context")],
    ))
    return cases


def _turns_to_flat_text(turns: List[Tuple[str, str]]) -> str:
    return "\n".join(
        f"[{'User' if r == 'user' else 'Assistant'}]: {c}" for r, c in turns
    )


# ==================== 判定 ====================


@dataclass
class CaseResult:
    cid: str
    category: str
    title: str
    probe_total: int
    v1_loose: int
    v2_loose: int
    v2_strict: int
    strict_eligible: int
    v1_tokens: int
    v2_tokens: int
    v1_latency_ms: int
    v2_latency_ms: int
    v2_healing: str
    v2_written: int
    v2_blocked: int
    v1_final: str
    v2_final: str


def _extract_v2_state(service) -> Optional[Dict]:
    latest = service._find_latest_summary("bench-conv", "bench-user")
    if not latest:
        return None
    structured = latest.get("structured_content")
    if isinstance(structured, str):
        try:
            structured = json.loads(structured)
        except json.JSONDecodeError:
            return None
    return structured if isinstance(structured, dict) else None


def _loose_hit(probe: Probe, text: str) -> bool:
    return probe.text in (text or "")


def _strict_hit(probe: Probe, state: Optional[Dict]) -> bool:
    if probe.expected_slot is None or state is None:
        return False
    val = state.get(probe.expected_slot)
    if isinstance(val, list):
        return any(probe.text in str(item) for item in val)
    if isinstance(val, str):
        return probe.text in val
    return False


# ==================== 单 case 多轮跑 ====================


def run_one_case(case: BenchCase, llm: OllamaChatClient) -> CaseResult:
    probes = case.probes
    probe_total = len(probes)
    strict_eligible = sum(1 for p in probes if p.expected_slot is not None)

    # V1
    v1 = V1CompactionSimulator(llm)
    v1_latency = 0
    for round_turns in case.rounds:
        started = time.perf_counter()
        asyncio.run(v1.compress_once(_turns_to_flat_text(round_turns)))
        v1_latency += int((time.perf_counter() - started) * 1000)
    v1_final = v1.load_context()
    v1_loose = sum(1 for p in probes if _loose_hit(p, v1_final))
    v1_tokens = llm.estimate_tokens(v1_final)

    # V2
    service, db = _build_v2_service(llm)
    seq = 1
    v2_latency = 0
    v2_written = 0
    v2_blocked = 0
    for round_idx, round_turns in enumerate(case.rounds):
        for role, content in round_turns:
            db.insert_row(
                "t_message",
                {
                    "id": f"{seq:05d}",
                    "conversation_id": "bench-conv",
                    "user_id": "bench-user",
                    "role": role,
                    "content": content,
                    "sources": [],
                    "deleted": 0,
                },
            )
            seq += 1
        rows_before = len(db.select_rows("t_conversation_summary"))
        calls_before = llm.call_count
        started = time.perf_counter()
        service._do_compress("bench-conv", "bench-user")
        v2_latency += int((time.perf_counter() - started) * 1000)
        rows_after = len(db.select_rows("t_conversation_summary"))
        calls_after = llm.call_count
        if rows_after > rows_before:
            v2_written += 1
        else:
            # LLM 有调用但未写行 → merge 违规拦截或 parse fail
            if calls_after > calls_before and round_idx > 0:
                v2_blocked += 1

    v2_state = _extract_v2_state(service)
    msg = service.load_latest_summary("bench-conv", "bench-user")
    v2_final = (msg.content if msg else "") or ""
    v2_loose = sum(1 for p in probes if _loose_hit(p, v2_final))
    v2_strict = sum(1 for p in probes if _strict_hit(p, v2_state))
    v2_tokens = llm.estimate_tokens(v2_final)
    latest = service._find_latest_summary("bench-conv", "bench-user") or {}
    healing = str(latest.get("healing_level") or "n/a")

    return CaseResult(
        cid=case.cid, category=case.category, title=case.title,
        probe_total=probe_total,
        v1_loose=v1_loose, v2_loose=v2_loose, v2_strict=v2_strict,
        strict_eligible=strict_eligible,
        v1_tokens=v1_tokens, v2_tokens=v2_tokens,
        v1_latency_ms=v1_latency, v2_latency_ms=v2_latency,
        v2_healing=healing, v2_written=v2_written, v2_blocked=v2_blocked,
        v1_final=v1_final, v2_final=v2_final,
    )


# ==================== 报告 ====================


def _print_table(results: List[CaseResult]) -> None:
    header = (
        f"{'CID':<4}{'Cat':<13}{'V1':<7}{'V2 loose':<11}{'V2 strict':<11}"
        f"{'V1 Tok':<8}{'V2 Tok':<8}{'V1 ms':<8}{'V2 ms':<8}{'Wr/Bk':<8}"
    )
    print()
    print("=" * len(header))
    print("Mneme-rag V1 vs V2 上下文压缩真 LLM 对比报告 v2 (3 轮压缩 · 500 字预算 · slot 严格判定)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        v1 = f"{r.v1_loose}/{r.probe_total}"
        v2l = f"{r.v2_loose}/{r.probe_total}"
        v2s = (
            f"{r.v2_strict}/{r.strict_eligible}"
            if r.strict_eligible > 0
            else "-"
        )
        wb = f"{r.v2_written}/{r.v2_blocked}"
        print(
            f"{r.cid:<4}{r.category:<13}{v1:<7}{v2l:<11}{v2s:<11}"
            f"{r.v1_tokens:<8}{r.v2_tokens:<8}"
            f"{r.v1_latency_ms:<8}{r.v2_latency_ms:<8}{wb:<8}"
        )
    print("=" * len(header))

    total_probes = sum(r.probe_total for r in results)
    total_strict = sum(r.strict_eligible for r in results)
    v1_sum = sum(r.v1_loose for r in results)
    v2_loose_sum = sum(r.v2_loose for r in results)
    v2_strict_sum = sum(r.v2_strict for r in results)
    v1_tok = sum(r.v1_tokens for r in results)
    v2_tok = sum(r.v2_tokens for r in results)
    v1_lat = sum(r.v1_latency_ms for r in results)
    v2_lat = sum(r.v2_latency_ms for r in results)
    v2_written = sum(r.v2_written for r in results)
    v2_blocked = sum(r.v2_blocked for r in results)
    print()
    v1_rate = 100.0 * v1_sum / max(1, total_probes)
    v2l_rate = 100.0 * v2_loose_sum / max(1, total_probes)
    v2s_rate = 100.0 * v2_strict_sum / max(1, total_strict) if total_strict else 0
    print(f"V1 loose        : {v1_sum}/{total_probes} = {v1_rate:.1f}%")
    print(f"V2 loose        : {v2_loose_sum}/{total_probes} = {v2l_rate:.1f}%   Δ={v2l_rate - v1_rate:+.1f} pp")
    print(f"V2 strict (slot): {v2_strict_sum}/{total_strict} = {v2s_rate:.1f}%")
    print(f"Total tokens    : V1={v1_tok}  V2={v2_tok}  Δ={v2_tok - v1_tok:+d}")
    print(f"Total latency   : V1={v1_lat} ms  V2={v2_lat} ms  Δ={v2_lat - v1_lat:+d} ms")
    print(f"V2 rounds       : 写入 {v2_written} / merge-blocked {v2_blocked} (共 {3 * len(results)} 轮)")


def _print_details(results: List[CaseResult]) -> None:
    print()
    print("每个 case 最终摘要（各取前 80 字符）：")
    print("-" * 78)
    for r in results:
        print(f"[{r.cid}] {r.title}")
        print(f"  V1: {r.v1_final[:80]!r}")
        print(f"  V2: {r.v2_final[:80]!r}")


# ==================== 多轮聚合（--repeat） ====================


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _pstdev(xs: List[float]) -> float:
    """总体标准差；样本 < 2 时返回 0"""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def _print_aggregate_table(runs: List[List[CaseResult]]) -> None:
    """
    按 case 聚合 N 次运行结果：mean ± stdev 与 min-max。
    末尾给整体聚合与"稳定赢/波动/稳定输"判定。
    """
    n = len(runs)
    cases_count = len(runs[0])
    header = (
        f"{'CID':<4}{'Cat':<13}"
        f"{'V1 mean[min-max]':<22}{'V2l mean[min-max]':<22}"
        f"{'V2s mean[min-max]':<22}{'Δmean':<8}"
    )
    print()
    print("=" * len(header))
    print(f"Mneme-rag V1 vs V2 多轮聚合报告（{n} 次运行 · mean ± stdev · 7b · 3 轮压缩 · 500 字）")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    overall_v1: List[float] = []
    overall_v2l: List[float] = []
    overall_v2s: List[float] = []
    overall_tok_v1: List[float] = []
    overall_tok_v2: List[float] = []
    overall_lat_v1: List[float] = []
    overall_lat_v2: List[float] = []

    for i in range(cases_count):
        run_results = [runs[r][i] for r in range(n)]
        total = run_results[0].probe_total
        strict_total = run_results[0].strict_eligible
        v1_scores = [r.v1_loose for r in run_results]
        v2l_scores = [r.v2_loose for r in run_results]
        v2s_scores = [r.v2_strict for r in run_results]
        v1_m = _mean(v1_scores)
        v2l_m = _mean(v2l_scores)
        v2s_m = _mean(v2s_scores)
        overall_v1.append(v1_m)
        overall_v2l.append(v2l_m)
        overall_v2s.append(v2s_m)
        overall_tok_v1.append(_mean([r.v1_tokens for r in run_results]))
        overall_tok_v2.append(_mean([r.v2_tokens for r in run_results]))
        overall_lat_v1.append(_mean([r.v1_latency_ms for r in run_results]))
        overall_lat_v2.append(_mean([r.v2_latency_ms for r in run_results]))
        v1_cell = f"{v1_m:.1f}±{_pstdev(v1_scores):.1f}[{min(v1_scores)}-{max(v1_scores)}]"
        v2l_cell = f"{v2l_m:.1f}±{_pstdev(v2l_scores):.1f}[{min(v2l_scores)}-{max(v2l_scores)}]"
        v2s_cell = (
            f"{v2s_m:.1f}±{_pstdev(v2s_scores):.1f}[{min(v2s_scores)}-{max(v2s_scores)}]"
            if strict_total > 0
            else "-"
        )
        delta = v2l_m - v1_m
        print(
            f"{run_results[0].cid:<4}{run_results[0].category:<13}"
            f"{v1_cell:<22}{v2l_cell:<22}{v2s_cell:<22}{delta:+.1f}"
        )
    print("=" * len(header))

    total_probes = run_results[0].probe_total * cases_count  # placeholder overwritten below
    # 用第一轮各 case 的 probe_total 汇总（各 run 相同）
    total_probes = sum(runs[0][i].probe_total for i in range(cases_count))
    total_strict = sum(runs[0][i].strict_eligible for i in range(cases_count))
    v1_sum = sum(overall_v1)
    v2l_sum = sum(overall_v2l)
    v2s_sum = sum(overall_v2s)
    print()
    print(f"整体聚合（{n} 次运行平均）:")
    print(
        f"  V1 loose        : {v1_sum:.1f}/{total_probes} = "
        f"{100.0 * v1_sum / total_probes:.1f}%"
    )
    print(
        f"  V2 loose        : {v2l_sum:.1f}/{total_probes} = "
        f"{100.0 * v2l_sum / total_probes:.1f}%   "
        f"Δ={100.0 * (v2l_sum - v1_sum) / total_probes:+.1f} pp"
    )
    if total_strict > 0:
        print(
            f"  V2 strict (slot): {v2s_sum:.1f}/{total_strict} = "
            f"{100.0 * v2s_sum / total_strict:.1f}%"
        )
    print(f"  Tokens (均)     : V1={sum(overall_tok_v1):.0f}  V2={sum(overall_tok_v2):.0f}")
    print(
        f"  Latency (均, ms): V1={sum(overall_lat_v1):.0f}  V2={sum(overall_lat_v2):.0f}"
    )

    # per-run 整体分数看波动
    per_run_v1 = []
    per_run_v2l = []
    for run in runs:
        tp = sum(r.probe_total for r in run)
        per_run_v1.append(100.0 * sum(r.v1_loose for r in run) / tp)
        per_run_v2l.append(100.0 * sum(r.v2_loose for r in run) / tp)
    print()
    print(f"每轮整体分:  V1={['{:.0f}'.format(x) for x in per_run_v1]}%")
    print(f"             V2={['{:.0f}'.format(x) for x in per_run_v2l]}%")
    print(
        f"run-to-run 方差: V1 σ={_pstdev(per_run_v1):.1f}pp, "
        f"V2 σ={_pstdev(per_run_v2l):.1f}pp"
    )

    # 逐 case 稳定性判定
    print()
    print("逐 case 稳定性判定（V2 - V1 的 mean 与波动）:")
    for i in range(cases_count):
        run_results = [runs[r][i] for r in range(n)]
        deltas = [r.v2_loose - r.v1_loose for r in run_results]
        dm = _mean(deltas)
        ds = _pstdev(deltas)
        total = run_results[0].probe_total
        if all(d > 0 for d in deltas):
            verdict = "稳定赢"
        elif dm > 0 and ds <= 0.5:
            verdict = "多数赢（偶有波动）"
        elif all(d == 0 for d in deltas):
            verdict = "持平"
        elif dm < 0 and all(d <= 0 for d in deltas):
            verdict = "稳定输"
        else:
            verdict = "波动大需加样本"
        print(
            f"  {run_results[0].cid} {run_results[0].category:<13}"
            f"Δ={dm:+.1f}±{ds:.1f} (每次满分 {total}) → {verdict}"
        )


# ==================== main ====================


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama-url", default="http://localhost:11434/v1")
    parser.add_argument("--ollama-model", default="qwen2.5:7b")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--repeat", type=int, default=1, help="全部 case 重复跑 N 遍取均值, 消除 run-to-run 方差"
    )
    args = parser.parse_args()

    llm = OllamaChatClient(base_url=args.ollama_url, model=args.ollama_model)

    print(f"连接测试: {args.ollama_url} model={args.ollama_model}")
    try:
        from core.llm.schema import ChatRequest, Message

        probe = ChatRequest(
            messages=[Message.user("只回复两个字: OK")],
            temperature=0.1, topP=0.9, thinking=False,
        )
        resp = asyncio.run(llm.chat(probe))
        print(f"  → {resp[:40]!r}")
    except Exception as ex:  # noqa: BLE001
        print(f"  连接失败: {ex}", file=sys.stderr)
        return 2

    cases = _make_cases()
    if args.limit > 0:
        cases = cases[: args.limit]

    if args.repeat > 1:
        runs: List[List[CaseResult]] = []
        for run_idx in range(args.repeat):
            print(f"\n===== 第 {run_idx + 1}/{args.repeat} 遍 =====")
            results: List[CaseResult] = []
            for case in cases:
                print(f"  → 跑 {case.cid} ({case.category}) {case.title} ...")
                results.append(run_one_case(case, llm))
            runs.append(results)
        _print_aggregate_table(runs)
        return 0

    results = []
    for case in cases:
        print(f"  → 跑 {case.cid} ({case.category}) {case.title} ...")
        results.append(run_one_case(case, llm))

    _print_table(results)
    if not args.quiet:
        _print_details(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
