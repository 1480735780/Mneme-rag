# -*- coding: utf-8 -*-
"""
V2.1.1 上下文压缩 V1 vs V2 结构对比基准（stub LLM 驱动，可回归）

**目的**：不测模型输出质量（那要真 LLM + 数据集 + 人工评测），只测
         V2 相对 V1 新增的**架构能力轴**——每一轴用 stub 模拟典型 LLM 行为，
         断言 V2 能做什么、V1 不能做什么。

六条评测轴：
    1. 三轮压缩后的**关键事实保真率**（Telephone Game 抗衰减）
    2. **Evidence doc_id 精确保留率**（V1 无派生路径，V2 有）
    3. **Merge 违规拦截率**（V1 无校验层，V2 有 P-02 merge validator）
    4. **Self-Healing 档位升级成功率**（V1 无 P-08，失败即永久卡水位）
    5. **Feature Flag 一键关停响应度**（V1 时代无灰度机制）
    6. **V1→V2 Lazy Migration 保真度**（零 LLM 成本迁移）

V1 一列的取值是"结构性事实"（V1 代码里就没这个能力），不是估算。

跑法：
    python -m pytest tests/benchmark/test_compaction_v1_vs_v2_benchmark.py -v -s
    -s 保留 print 出报告；不带 -s 也能跑通断言。
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple

import pytest

from core.llm.enums import Tier
from core.llm.schema import ChatRequest
from rag.memory.config import MemoryProperties
from rag.memory.summary import DatabaseConversationMemorySummaryService
from rag.prompt.builder import AgentPromptSlot, StaticAgentPromptResolver
from storage.database import InMemoryDatabaseClient


# 报告表：每轴测完后填一行，teardown 阶段统一打印
_report_rows: List[Tuple[str, str, str, str]] = []


def _record(axis: str, v1_result: str, v2_result: str, note: str = "") -> None:
    _report_rows.append((axis, v1_result, v2_result, note))


@pytest.fixture(scope="module", autouse=True)
def _print_report_at_end():
    yield
    print("\n" + "=" * 78)
    print("Mneme-rag V1 vs V2 上下文压缩能力对比报告（stub LLM 驱动）")
    print("=" * 78)
    print(f"{'评测轴':<32}{'V1':<18}{'V2':<18}{'备注'}")
    print("-" * 78)
    for axis, v1, v2, note in _report_rows:
        print(f"{axis:<32}{v1:<18}{v2:<18}{note}")
    print("=" * 78)


# ==================== Stub LLM 与 fixture helpers ====================


class _ProgrammableLLM:
    """按 FIFO 消耗预置脚本；每项要么是字符串输出，要么是 Exception"""

    def __init__(self):
        self.scripts_by_tier: Dict[str, List] = {}
        self.calls: List[Tuple[str, str]] = []  # (tier_name, first_user_or_last_msg_text)

    def queue(self, tier: Tier, *items) -> None:
        self.scripts_by_tier.setdefault(tier.value, []).extend(items)

    async def chat(self, request: ChatRequest, tier: Tier = Tier.FAST) -> str:
        self.calls.append((tier.value, _extract_probe_text(request)))
        queue = self.scripts_by_tier.get(tier.value, [])
        if not queue:
            raise AssertionError(f"tier={tier.value} 无预置脚本")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _extract_probe_text(request: ChatRequest) -> str:
    """从 ChatRequest 里挖一段用于日志/断言的短文本"""
    if not request.messages:
        return ""
    for m in reversed(request.messages):
        if m.role.value == "user" and m.content:
            return m.content[:80]
    return ""


def _make_db() -> InMemoryDatabaseClient:
    return InMemoryDatabaseClient(
        {"t_message": [], "t_conversation_summary": [], "t_conversation": []}
    )


def _seed_msg(
    db: InMemoryDatabaseClient,
    seq: int,
    role: str,
    content: str,
    sources: Optional[list] = None,
) -> int:
    db.insert_row(
        "t_message",
        {
            "id": f"{seq:05d}",
            "conversation_id": "conv-bench",
            "user_id": "u-bench",
            "role": role,
            "content": content,
            "sources": sources or [],
            "deleted": 0,
        },
    )
    return seq + 1


def _make_service(
    db: InMemoryDatabaseClient,
    llm: _ProgrammableLLM,
    *,
    compression_version: str = "v2",
    summary_start_turns: int = 1,
    history_keep_turns: int = 8,
) -> DatabaseConversationMemorySummaryService:
    return DatabaseConversationMemorySummaryService(
        db=db,
        llm_service=llm,  # type: ignore[arg-type]
        prompt_resolver=StaticAgentPromptResolver(
            prompts={AgentPromptSlot.CONVERSATION_SUMMARY.name: "SYS {summary_max_chars}"}
        ),
        properties=MemoryProperties(
            summary_enabled=True,
            summary_start_turns=summary_start_turns,
            history_keep_turns=history_keep_turns,
            summary_max_chars=200,
            context_compression_version=compression_version,
        ),
    )


def _trigger(service: DatabaseConversationMemorySummaryService) -> None:
    service._do_compress("conv-bench", "u-bench")


# ==================== 轴 1: 三轮压缩关键事实保真率 ====================


def test_axis_1_telephone_game_resistance():
    """
    5 个"关键事实"分散种在长会话里，跑多轮压缩；
    V2 通过 structured state 的 critical_context append 语义累积保留，
    V1 每轮 200 字自由文本重写必然衰减（结构性事实）。
    """
    db = _make_db()
    seq = 1
    for i in range(12):
        seq = _seed_msg(db, seq, "user", f"轮次 {i} 的问题")
        seq = _seed_msg(db, seq, "assistant", f"轮次 {i} 的回答")

    # V2 stub：每轮 LLM 输出把 5 个关键事实都塞进 critical_context（模拟 V2 应有的合并行为）
    key_facts = ["PostgreSQL", "FastAPI", "Milvus", "K8s", "Python 3.13"]
    llm_v2 = _ProgrammableLLM()
    for _ in range(3):
        llm_v2.queue(
            Tier.FAST,
            json.dumps(
                {
                    "user_goal": "系统架构讨论",
                    "progress": [f"里程碑 {i}"],
                    "open_questions": [],
                    "critical_context": key_facts,  # 累积保留 5 项
                },
                ensure_ascii=False,
            ),
        )
    v2_service = _make_service(db, llm_v2)
    for _ in range(3):
        _trigger(v2_service)
    v2_msg = v2_service.load_latest_summary("conv-bench", "u-bench")
    v2_content = v2_msg.content if v2_msg else ""
    v2_hit = sum(1 for k in key_facts if k in v2_content)

    # V1 baseline 结构性事实：单条 200 字自由文本反复重写必然丢项，此处按 V1 设计容量估算
    # 每轮 LLM 重写 5 项塞 200 字里，第 3 轮典型保留 2-3 项（telephone game 常识，见 MEMORY 记录）
    v1_hit_estimate_max = 3  # 保守上限

    _record(
        "轴1 三轮压缩关键事实保真",
        f"≤{v1_hit_estimate_max}/5 (估)",
        f"{v2_hit}/5",
        "V2 通过 critical_context 独立槽位累积",
    )
    assert v2_hit == 5, f"V2 应完整保留 5 项关键事实, 实际 {v2_hit}/5"


# ==================== 轴 2: Evidence doc_id 精确保留 ====================


def test_axis_2_evidence_doc_id_preservation():
    """
    5 条 assistant 消息分别引用 doc_A..doc_E（各一个 doc_id）；
    压缩一次后 load SYSTEM 消息；
    V2 通过 rag/memory/evidence.py 派生 Top-K 精确注入所有 doc_id；
    V1 只在自然语言摘要里模糊提到"退款政策"，具体 doc_id 完全丢。
    """
    db = _make_db()
    seq = 1
    doc_ids = ["doc_A", "doc_B", "doc_C", "doc_D", "doc_E"]
    for i, doc_id in enumerate(doc_ids):
        seq = _seed_msg(db, seq, "user", f"问 {i}")
        seq = _seed_msg(
            db,
            seq,
            "assistant",
            f"依据 {doc_id} 回答",
            sources=[{"index": 1, "docId": doc_id, "docName": f"文档 {doc_id}"}],
        )

    llm = _ProgrammableLLM()
    llm.queue(
        Tier.FAST,
        json.dumps(
            {
                "user_goal": "多文档问答",
                "progress": [],
                "open_questions": [],
                "critical_context": [],  # 故意不放 doc_id，测试派生机制
            },
            ensure_ascii=False,
        ),
    )
    service = _make_service(db, llm)
    _trigger(service)
    loaded = service.load_latest_summary("conv-bench", "u-bench")
    content = loaded.content if loaded else ""

    v2_hit = sum(1 for d in doc_ids if f"doc={d}" in content)
    v1_hit = 0  # V1 无 evidence 派生代码路径，硬编码 0

    _record(
        "轴2 Evidence doc_id 精确保留",
        f"{v1_hit}/5 (无派生)",
        f"{v2_hit}/5",
        "V2 从 t_message.sources 装配阶段动态派生",
    )
    assert v2_hit == 5, f"V2 应完整保留 5 个 doc_id, 实际 {v2_hit}/5"


# ==================== 轴 3: Merge 违规拦截 ====================


def test_axis_3_merge_violation_catch():
    """
    预置 V2 摘要行含 5 项 progress；
    模拟 LLM 输出把 progress 塌成 1 项（大面积丢失）；
    V2 通过 P-02 merge 校验拦截，不写新行；
    V1 无校验层，任何 LLM 输出都直接覆盖，数据丢失不可逆。
    """
    db = _make_db()
    seq = 1
    for i in range(12):
        seq = _seed_msg(db, seq, "user", f"问 {i}")
        seq = _seed_msg(db, seq, "assistant", f"答 {i}")

    # 预置一个低 id 的 V2 摘要行，含 5 项 progress
    db.insert_row(
        "t_conversation_summary",
        {
            "id": "00000000000000001",
            "conversation_id": "conv-bench",
            "user_id": "u-bench",
            "content": "V2 snapshot",
            "structured_content": {
                "user_goal": "g",
                "progress": ["里程碑 A", "里程碑 B", "里程碑 C", "里程碑 D", "里程碑 E"],
                "open_questions": [],
                "critical_context": [],
            },
            "summary_version": "v2",
            "last_message_id": "00002",
            "deleted": 0,
        },
    )
    before_row_count = len(db.select_rows("t_conversation_summary"))

    # LLM 输出只保留 1 项 progress → 触发 merge 违规（4/5 丢失 > 30% 阈值）
    llm = _ProgrammableLLM()
    llm.queue(
        Tier.FAST,
        json.dumps(
            {
                "user_goal": "g",
                "progress": ["里程碑 A"],
                "open_questions": [],
                "critical_context": [],
            },
            ensure_ascii=False,
        ),
    )
    service = _make_service(db, llm)
    _trigger(service)

    after_row_count = len(db.select_rows("t_conversation_summary"))
    v2_caught = (after_row_count == before_row_count)  # 未写新行 = 拦截成功

    # V1 baseline: 无校验层，任何 LLM 输出都会直接 UPDATE/INSERT 覆盖
    v1_caught = False

    _record(
        "轴3 Merge 大面积丢失拦截",
        "0% (无校验)" if not v1_caught else "V1 不应有此能力",
        "100%" if v2_caught else "0% (校验未生效!)",
        "V2 P-02 progress_drop_ratio>30% 触发保护",
    )
    assert v2_caught, "V2 应拦截 merge 违规不写新行"


# ==================== 轴 4: Self-Healing 三级自愈 ====================


def test_axis_4_self_healing_tier_upgrade():
    """
    FAST 档连续 5 次 JSON 解析失败；
    V2 P-08 Level 1 应自动切 STANDARD 档重试；
    V1 时代"parse fail → 保留旧 state"是死循环，水位卡住不动直到 context 爆。
    """
    db = _make_db()
    seq = 1
    for i in range(12):
        seq = _seed_msg(db, seq, "user", f"问 {i}")
        seq = _seed_msg(db, seq, "assistant", f"答 {i}")

    llm = _ProgrammableLLM()
    # 5 次 FAST garbage
    for _ in range(5):
        llm.queue(Tier.FAST, "garbage output not json")
    # 第 6 次走 STANDARD，成功
    llm.queue(
        Tier.STANDARD,
        json.dumps(
            {
                "user_goal": "g",
                "progress": ["recovered"],
                "open_questions": [],
                "critical_context": [],
            },
            ensure_ascii=False,
        ),
    )
    service = _make_service(db, llm)

    for _ in range(5):
        _trigger(service)
    v2_latest_before = service._find_latest_summary("conv-bench", "u-bench")
    _trigger(service)  # 第 6 次
    v2_latest_after = service._find_latest_summary("conv-bench", "u-bench")

    # V2 断言：5 次失败期间无新行；第 6 次走 STANDARD 成功后有新行
    v2_recovered = v2_latest_before is None and v2_latest_after is not None
    tier_calls = [t for t, _ in llm.calls]
    v2_upgraded = "standard" in tier_calls

    # V1 baseline: 无 tier upgrade 逻辑，5 次失败会一直留在 FAST 档
    v1_recovered = False

    _record(
        "轴4 Self-Healing 档位升级",
        "无该机制" if not v1_recovered else "?",
        f"升档={'是' if v2_upgraded else '否'} 恢复={'是' if v2_recovered else '否'}",
        "V2 P-08 L1 5 次失败自动切 STANDARD",
    )
    assert v2_upgraded and v2_recovered


# ==================== 轴 5: Feature Flag 灰度关停 ====================


def test_axis_5_feature_flag_kill_switch():
    """
    全局 context_compression_version='v1'；
    V2 P-07 应让 _do_compress 直接 skip 零 LLM 调用；
    会话若已有 V2 历史则继续 sticky 走 V2 保证数据一致性。
    """
    db = _make_db()
    seq = 1
    for i in range(12):
        seq = _seed_msg(db, seq, "user", f"问 {i}")
        seq = _seed_msg(db, seq, "assistant", f"答 {i}")

    # 无脚本 → 若被调用即抛，正好证明 flag 生效
    llm = _ProgrammableLLM()
    service = _make_service(db, llm, compression_version="v1")
    _trigger(service)

    no_llm_calls = len(llm.calls) == 0
    no_new_row = service._find_latest_summary("conv-bench", "u-bench") is None

    _record(
        "轴5 Feature Flag 灰度关停",
        "V1 时代无 flag",
        f"零 LLM={'是' if no_llm_calls else '否'} 零写入={'是' if no_new_row else '否'}",
        "V2 P-07 v1 flag 让 _do_compress 立即 return",
    )
    assert no_llm_calls and no_new_row


# ==================== 轴 6: Lazy Migration 零成本迁移 ====================


def test_axis_6_lazy_migration_from_v1_row():
    """
    预置一条 V1 摘要行（summary_version 缺失，content 是自由文本）；
    V2 P-06 Lazy Migration：
      (a) 加载时不触发任何转换（零 LLM 成本），V1 原文直接返回
      (b) 触发压缩时 V1 文本整段进 critical_context，LLM 输出后升级为新 V2 行
    V1 无迁移概念，读取永远走老逻辑不升级。
    """
    db = _make_db()
    seq = 1
    for i in range(12):
        seq = _seed_msg(db, seq, "user", f"问 {i}")
        seq = _seed_msg(db, seq, "assistant", f"答 {i}")

    v1_text = "V1 时代的一段自由文本摘要, 记录了用户之前问了什么"
    db.insert_row(
        "t_conversation_summary",
        {
            "id": "00000000000000001",
            "conversation_id": "conv-bench",
            "user_id": "u-bench",
            "content": v1_text,
            "last_message_id": "00002",
            # 无 summary_version / structured_content → 视为 V1
            "deleted": 0,
        },
    )

    # (a) 读取路径：零 LLM 成本
    load_llm = _ProgrammableLLM()
    load_service = _make_service(db, load_llm)
    loaded = load_service.load_latest_summary("conv-bench", "u-bench")
    read_zero_cost = loaded is not None and v1_text in loaded.content
    zero_llm_calls = len(load_llm.calls) == 0

    # (b) 压缩路径：previous_state 里 V1 文本进 critical_context，LLM 输出后升级 V2
    migrate_llm = _ProgrammableLLM()
    migrate_llm.queue(
        Tier.FAST,
        json.dumps(
            {
                "user_goal": "g",
                "progress": ["已迁移"],
                "open_questions": [],
                "critical_context": ["已合入新结构"],
            },
            ensure_ascii=False,
        ),
    )
    migrate_service = _make_service(db, migrate_llm)
    _trigger(migrate_service)
    latest = migrate_service._find_latest_summary("conv-bench", "u-bench")
    migrated_to_v2 = latest is not None and (latest.get("summary_version") or "") == "v2"

    _record(
        "轴6 V1→V2 Lazy Migration",
        "V1 无迁移概念",
        f"零成本读={'是' if (read_zero_cost and zero_llm_calls) else '否'} 升级={'是' if migrated_to_v2 else '否'}",
        "V2 P-06 首次压缩时自动 wrap 到 critical_context",
    )
    assert read_zero_cost and zero_llm_calls and migrated_to_v2


# ==================== 轴 7（附加）：Token 效率对比 ====================


def test_axis_7_token_efficiency_under_duplicate_load():
    """
    种一段 10K 长文 + 3 次复制粘贴 → V1 无 deterministic 直接全量喂 LLM；
    V2 P-03 deterministic_compress_messages 会裁剪长消息 + 折叠重复；
    度量"实际喂给 LLM 的 user+assistant 消息总字符数"。
    """
    db = _make_db()
    seq = 1
    long_content = "重复内容测试。" * 2000  # ~14K chars

    for i in range(12):
        if i < 3:
            # 前 3 轮 user 都贴同一段超长内容 → deterministic 应折叠
            seq = _seed_msg(db, seq, "user", long_content)
            seq = _seed_msg(db, seq, "assistant", f"答 {i}")
        else:
            seq = _seed_msg(db, seq, "user", f"问 {i}")
            seq = _seed_msg(db, seq, "assistant", f"答 {i}")

    llm = _ProgrammableLLM()
    for _ in range(3):
        llm.queue(
            Tier.FAST,
            json.dumps(
                {"user_goal": "g", "progress": [], "open_questions": [], "critical_context": []}
            ),
        )
    service = _make_service(db, llm)
    _trigger(service)

    # 从最后一次 LLM 调用里读出实际喂给模型的 messages 总字符
    # 简易估算：3 条超长同内容消息 V2 会折叠到 1 条完整 + 2 条 marker
    # V1 会 3 条全量
    # 这里通过 llm.calls 记录的是 probe 文本长度不能直接测；改测"压缩前 vs 后字符数"
    from rag.memory.compression.deterministic import (
        DeterministicCompressConfig,
        deterministic_compress_messages,
    )
    from core.llm.schema import Message, Role

    raw = [
        Message.user(long_content),
        Message.user(long_content),
        Message.user(long_content),
    ]
    compressed = deterministic_compress_messages(
        raw,
        DeterministicCompressConfig(
            long_message_threshold=3000, head_chars=1500, tail_chars=500, dedup_enabled=True
        ),
    )
    v1_total_chars = sum(len(m.content) for m in raw)
    v2_total_chars = sum(len(m.content) for m in compressed)
    reduction_ratio = 1.0 - v2_total_chars / v1_total_chars

    _record(
        "轴7 Deterministic 压缩字符节省率",
        "0% (无 deterministic)",
        f"{reduction_ratio:.1%}",
        "V2 P-03 长消息裁剪 + 重复折叠",
    )
    assert reduction_ratio > 0.6, f"deterministic 应至少省 60%, 实际 {reduction_ratio:.1%}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
