# -*- coding: utf-8 -*-
"""
P10.3 T3.2 单测：YamlSnapshotPricer 成本计算器（D7：纯内存查快照，固化点填数）

覆盖：
    - 计价：token 成本 = (in_tokens*in_price + out_tokens*out_price) / 1e6，6 位小数；
      快照记「生效的价格账本」（version/currency/模型/计价维度/单价），成本落行；
    - 别名解析进快照（model 为规范 id）；
    - miss 即 unpriced：模型未登记 / 行缺 token / 空模型——快照仍落库并带 reason
      （「无价」与「无记录」可区分；0 从不伪造免费）；
    - 发射方已带快照的行原样放行（T1.8 契约不回归）；
    - D7 per-image 预留：ext.image_count 计价生效；未携 image_count 回落 token 计价
      （无 token 价则 unpriced，reason=per_image_without_image_count）；
    - 承诺 12 实测（改价后历史重聚合不漂移）：v1 价格落的行固化后，换 v2 价落新行，
      合口聚合数字 = 逐行已固化 cost 之和且两次重读一致——看上去聚合只读列，从未重算；
    - wiring 装配：出厂样例（空价格表）→ hub 注入 YamlSnapshotPricer 且行为=unpriced。
异步统一 asyncio.run() 包裹（房式约定，对齐 test_observation_pricing_unit）。
"""
import asyncio
import json

from app.config import AppSettings
from app.wiring import AppContainer
from core.llm.observation import (
    CALL_KIND_CHAT,
    CALL_KIND_VLM,
    LLM_CALL_TABLE,
    CallEvent,
    LlmCallDao,
    ObservationHub,
)
from core.llm.observation.pricing import PRICING_STATUS_PRICED, PRICING_STATUS_UNPRICED, YamlSnapshotPricer
from core.llm.observation.pricing_config import load_pricing_from_dict
from storage.database import InMemoryDatabaseClient


def _pricer() -> YamlSnapshotPricer:
    return YamlSnapshotPricer(load_pricing_from_dict(_price_book("v1")))


def _price_book(version: str, *, input_price: float = 1.6, output_price: float = 6.4) -> dict:
    return {
        "pricing": {
            "pricing_version": version,
            "currency": "USD",
            "aliases": {"qwen-max-2025-xx": "qwen-max"},
            "models": {
                "qwen-max": {"input_per_million_usd": input_price, "output_per_million_usd": output_price},
            },
            "call_kinds": {
                "vlm": {
                    "billing_unit": "per_image",
                    "models": {"qwen-vl-max": {"per_image_usd": 0.02}},
                }
            },
        }
    }


def _event(**kw) -> CallEvent:
    payload = dict(
        request_uid="rq-pricer",
        call_kind=CALL_KIND_CHAT,
        provider="openai",
        model="qwen-max",
        status="success",
        scene="chat",
        purpose="generate",
        prompt_tokens=100,
        completion_tokens=200,
        token_source="real",
    )
    payload.update(kw)
    return CallEvent(**payload)


def _drain(hub: ObservationHub, db: InMemoryDatabaseClient):
    async def scenario():
        await hub.stop_and_drain()

    asyncio.run(scenario())
    return db.select_rows(LLM_CALL_TABLE)


# ==================== 计价与快照形状 ====================


def test_priced_row_cost_and_snapshot_book():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())
    hub.emit(_event())
    row = _drain(hub, db)[0]
    expected = round((100 * 1.6 + 200 * 6.4) / 1_000_000, 6)
    assert row["cost_usd"] == expected
    snapshot = json.loads(row["pricing_snapshot"])
    assert snapshot == {
        "pricing_version": "v1",
        "status": PRICING_STATUS_PRICED,
        "currency": "USD",
        "model": "qwen-max",
        "billing_unit": "tokens",
        "input_per_million_usd": 1.6,
        "output_per_million_usd": 6.4,
    }


def test_alias_resolved_into_snapshot():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())
    hub.emit(_event(model="qwen-max-2025-xx"))
    row = _drain(hub, db)[0]
    assert json.loads(row["pricing_snapshot"])["model"] == "qwen-max"


def test_rounding_to_micro_dollar():
    config = load_pricing_from_dict({"pricing": {
        "pricing_version": "v1",
        "models": {"m": {"input_per_million_usd": 0.4, "output_per_million_usd": 0.9}},
    }})
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=YamlSnapshotPricer(config))
    hub.emit(_event(model="m", prompt_tokens=333, completion_tokens=777))
    row = _drain(hub, db)[0]
    assert row["cost_usd"] == round((333 * 0.4 + 777 * 0.9) / 1_000_000, 6)


# ==================== miss → unpriced（带 reason，仍可追溯） ====================


def test_unpriced_when_model_missing_from_book():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())
    hub.emit(_event(model="unknown-model"))
    row = _drain(hub, db)[0]
    assert row["cost_usd"] is None  # 不伪造 0
    snapshot = json.loads(row["pricing_snapshot"])
    assert snapshot["status"] == PRICING_STATUS_UNPRICED
    assert snapshot["pricing_version"] == "v1"  # 版本账本仍记录：覆盖率可统计
    assert snapshot["reason"] == "model_unpriced"
    assert snapshot["model"] == "unknown-model"


def test_unpriced_when_row_lacks_tokens():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())
    hub.emit(_event(prompt_tokens=None, completion_tokens=None))
    row = _drain(hub, db)[0]
    snapshot = json.loads(row["pricing_snapshot"])
    assert snapshot["reason"] == "missing_tokens"
    assert row["cost_usd"] is None


def test_unpriced_when_model_field_empty():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())
    hub.emit(_event(model=""))
    row = _drain(hub, db)[0]
    assert json.loads(row["pricing_snapshot"])["reason"] == "missing_model"


def test_emitter_snapshot_still_respected():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())
    provided = {"pricing_version": "emitter-v9", "status": PRICING_STATUS_PRICED}
    hub.emit(_event(pricing_snapshot=provided))
    row = _drain(hub, db)[0]
    assert json.loads(row["pricing_snapshot"]) == provided
    assert row["cost_usd"] is None  # 固化点不越权替发射方定成本


def test_pricer_rejects_raw_dict_fail_fast():
    """构造期类型显式校验：原始 dict 必须被拒绝（防静默降级 unpriced 的错价数据进看板）"""
    import pytest

    from core.llm.observation.pricing_config import PricingConfigError

    with pytest.raises(PricingConfigError, match="PricingConfig"):
        YamlSnapshotPricer(_price_book("v1"))  # 原始 dict（未解析）——构造时轰鸣拒绝


# ==================== D7 per-image 预留 ====================


def test_per_image_pricing_with_image_count():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())
    hub.emit(_event(call_kind=CALL_KIND_VLM, model="qwen-vl-max", ext={"image_count": 3}))
    row = _drain(hub, db)[0]
    assert row["cost_usd"] == round(3 * 0.02, 6)
    snapshot = json.loads(row["pricing_snapshot"])
    assert snapshot["billing_unit"] == "per_image"
    assert snapshot["image_count"] == 3.0
    assert snapshot["per_image_usd"] == 0.02


def test_per_image_without_image_count_falls_back_to_token_pricing():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=_pricer())  # vlm 设 per_image；qwen-max 仅 token 价
    hub.emit(_event(call_kind=CALL_KIND_VLM, model="qwen-max"))  # 未携 image_count
    row = _drain(hub, db)[0]
    expected = round((100 * 1.6 + 200 * 6.4) / 1_000_000, 6)
    assert row["cost_usd"] == expected  # token 通用回落（qwen-max 在 token 表）
    assert json.loads(row["pricing_snapshot"])["billing_unit"] == "tokens"


def test_per_image_only_model_without_image_count_is_unpriced():
    config = load_pricing_from_dict({"pricing": {
        "pricing_version": "v1",
        "call_kinds": {"vlm": {"billing_unit": "per_image",
                               "models": {"vlm-only": {"per_image_usd": 0.01}}}},
    }})
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), pricer=YamlSnapshotPricer(config))
    hub.emit(_event(call_kind=CALL_KIND_VLM, model="vlm-only"))
    row = _drain(hub, db)[0]
    snapshot = json.loads(row["pricing_snapshot"])
    assert snapshot["reason"] == "per_image_without_image_count"
    assert row["cost_usd"] is None


# ==================== 承诺 12：改价后历史重聚合不漂移 ====================


def test_promise_12_reaggregation_after_price_change_does_not_drift():
    """v1 价格落库固化后改价 v2：旧行成本/快照纹丝不动，新行按 v2 固化；
    看板聚合两次重读完全一致（聚合只读已固化列，从不按价格重算）"""
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub_v1 = ObservationHub(
        LlmCallDao(db), pricer=YamlSnapshotPricer(load_pricing_from_dict(_price_book("v1")))
    )
    hub_v1.emit(_event(request_uid="rq-v1"))
    rows_v1 = _drain(hub_v1, db)
    assert len(rows_v1) == 1
    v1_cost = rows_v1[0]["cost_usd"]

    hub_v2 = ObservationHub(
        LlmCallDao(db),
        pricer=YamlSnapshotPricer(load_pricing_from_dict(_price_book("v2", input_price=3.2, output_price=12.8))),
    )
    hub_v2.emit(_event(request_uid="rq-v2"))
    rows_all = _drain(hub_v2, db)
    assert len(rows_all) == 2

    v2_row = next(each for each in rows_all if each["request_uid"] == "rq-v2")
    v1_row = next(each for each in rows_all if each["request_uid"] == "rq-v1")
    # 旧行零漂移：成本与快照版本保持 v1 固化值
    assert v1_row["cost_usd"] == v1_cost
    assert json.loads(v1_row["pricing_snapshot"])["pricing_version"] == "v1"
    # 新行按 v2 价格固化（同 token 数，单价翻倍 → 成本翻倍）
    assert v2_row["cost_usd"] == round((100 * 3.2 + 200 * 12.8) / 1_000_000, 6)
    assert json.loads(v2_row["pricing_snapshot"])["pricing_version"] == "v2"

    # 重聚合（模拟看板按日/按模型聚合的重复执行）：两次结果恒等，且等于逐行已固化值之和
    def aggregate():
        rows = db.select_rows(LLM_CALL_TABLE)
        return {
            "total": round(sum(each["cost_usd"] for each in rows), 6),
            "priced_rows": sum(1 for each in rows if each["cost_usd"] is not None),
        }

    first = aggregate()
    second = aggregate()
    assert first == second
    assert first["priced_rows"] == 2
    assert first["total"] == round(v1_cost + v2_row["cost_usd"], 6)


# ==================== wiring 装配（出厂样例 → unpriced 行为） ====================


def test_wiring_injects_pricer_from_shipped_sample():
    """出厂样例（空价格表）也注入计算器：行为=全 unpriced 但快照带 blank 版本与 reason"""
    container = AppContainer._build_memory(AppSettings())
    from core.llm.observation.pricing import YamlSnapshotPricer as _P

    assert isinstance(container.observation_hub._pricer, _P)
    pricer = container.observation_hub._pricer
    row = {"call_kind": "chat", "model": "anything", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": None, "pricing_snapshot": None}
    pricer(row)
    snapshot = json.loads(row["pricing_snapshot"])
    assert snapshot["status"] == PRICING_STATUS_UNPRICED
    assert snapshot["pricing_version"] == "blank-initial"
    assert snapshot["reason"] == "model_unpriced"
    assert row["cost_usd"] is None
