# -*- coding: utf-8 -*-
"""P10.1 T1.8 单测：成本固化钩子（D7 预留，P10.3 T3.2 接管前 unpriced 占位）

覆盖：
    - unpriced 占位：无价行 pricing_snapshot 始终写（state 字面量 + version 字段位）、
      cost_usd 恒空（留空而非 0——0 会伪造免费）
    - 发射方已带快照的行原样放行（固化点不覆盖）
    - 注入 pricer 生效：cost_usd + 快照由钩子填写（P10.3 注入契约：行级、纯内存签名）
    - 钩子异常吞掉 + pricing_errors 计数（执行纪律 4），失败行照常落库并兜底 unpriced
      快照，/metrics 出口携带该计数（D13）
异步统一 asyncio.run() 包裹（房式约定）。
"""
import asyncio
import json

from core.llm.observation import (
    CALL_KIND_CHAT,
    LLM_CALL_TABLE,
    CallEvent,
    LlmCallDao,
    ObservationHub,
)
from core.llm.observation.pricing import PRICING_STATUS_UNPRICED, apply_unpriced, unpriced_snapshot
from storage.database import InMemoryDatabaseClient


def _event(**kw) -> CallEvent:
    payload = dict(
        request_uid="rq-pricing",
        call_kind=CALL_KIND_CHAT,
        provider="openai",
        model="gpt-x",
        status="success",
        scene="chat",
        purpose="generate",
        prompt_tokens=10,
        completion_tokens=5,
        token_source="real",
    )
    payload.update(kw)
    return CallEvent(**payload)


def _drain(hub: ObservationHub, db: InMemoryDatabaseClient):
    async def scenario():
        await hub.stop_and_drain()

    asyncio.run(scenario())
    return db.select_rows(LLM_CALL_TABLE)


# ==================== 默认行为（unpriced 占位） ====================


def test_unpriced_snapshot_always_written_and_cost_empty():
    """无价时代价快照占位（始终写）、cost_usd 恒空——可解释的「无价格」而非「无记录」"""
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db))
    hub.emit(_event())
    hub.emit(_event(request_uid="rq2", token_source="estimated"))
    rows = _drain(hub, db)
    assert len(rows) == 2
    for row in rows:
        snapshot = json.loads(row["pricing_snapshot"])  # 必非 NULL：字符串为合法 JSON
        assert snapshot == {"pricing_version": None, "status": PRICING_STATUS_UNPRICED}
        assert row["cost_usd"] is None  # 留空待 P10.3 填充，不伪造 0


def test_unit_helpers_agree_on_placeholder_shape():
    """占位快照形状收敛在 pricing.py 单点（apply_unpriced 与 unpriced_snapshot 同源）"""
    row = {"pricing_snapshot": None}
    apply_unpriced(row)
    assert json.loads(row["pricing_snapshot"]) == {"pricing_version": None, "status": "unpriced"}
    assert json.loads(row["pricing_snapshot"]) == unpriced_snapshot()


def test_emitter_snapshot_preserved():
    """发射方已带快照：固化点尊重原值，不覆盖（未来 emitter 直带价格的一律生效）"""
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db))
    provided = {"pricing_version": "yaml-v1", "input_price_usd": 2.0}
    hub.emit(_event(pricing_snapshot=provided))
    row = _drain(hub, db)[0]
    assert json.loads(row["pricing_snapshot"]) == provided
    assert row["cost_usd"] is None


# ==================== 注入 pricer（P10.3 T3.2 接管契约） ====================


def test_injected_pricer_fills_cost_and_snapshot():
    """注入 pricer：cost_usd + 快照由钩子填写（查 yaml 快照的纯内存计算器占位位）"""
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})

    def fake_pricer(row):
        row["cost_usd"] = 0.00015
        row["pricing_snapshot"] = json.dumps({"pricing_version": "yaml-v1", "status": "priced"})

    hub = ObservationHub(LlmCallDao(db), pricer=fake_pricer)
    hub.emit(_event())
    row = _drain(hub, db)[0]
    assert row["cost_usd"] == 0.00015
    assert json.loads(row["pricing_snapshot"]) == {"pricing_version": "yaml-v1", "status": "priced"}
    assert hub.stats.pricing_errors == 0


def test_pricer_exception_swallowed_counted_and_unpriced_fallback():
    """钩子异常：吞掉 + 计数（执行纪律 4），行照常落库并兜底 unpriced 快照，
    /metrics 出口携带 pricing_errors（D13 可观测的丢）"""
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})

    def broken_pricer(row):
        raise RuntimeError("pricing bug")

    hub = ObservationHub(LlmCallDao(db), pricer=broken_pricer)
    hub.emit(_event())
    row = _drain(hub, db)[0]
    assert row["cost_usd"] is None
    assert json.loads(row["pricing_snapshot"])["status"] == "unpriced"  # 故障行不留 NULL 快照
    assert hub.stats.pricing_errors == 1
    assert hub.stats.flush_errors == 0  # 行已正常落库：固化失败不升级为整批拒收
    assert hub.metrics()["observation"]["pricing_errors"] == 1
