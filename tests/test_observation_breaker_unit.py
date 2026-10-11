# -*- coding: utf-8 -*-
"""P10.1 T1.7 单测：熔断器状态观察者（D12 接缝）+ BreakerEvent + hub 分派 + /metrics 区块

覆盖：
    - 阈值触发 OPEN（CLOSED→open，快照 = 触发计数）
    - 探测恢复 HALF_OPEN→CLOSED（恢复事件）；CLOSED 内成功重置不产生事件
    - 探测失败 HALF_OPEN→OPEN（快照 0）
    - OPEN 超时转 HALF_OPEN（open→half_open）
    - observer 异常吞掉 + observer_errors 计数，熔断器本体不受影响（第一戒律）
    - snapshot_states 形态
    - hub 分派：BreakerEvent 进环形缓冲 + 计数；同批 CallEvent 照常落库
    - app 级 /metrics 带 breakers 区块
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from core.llm.model.health_store import HealthState, ModelHealthStore
from core.llm.observation import (
    LLM_CALL_TABLE,
    CallEvent,
    LlmCallDao,
    ObservationHub,
)
from core.llm.observation.events import BreakerEvent
from storage.database import InMemoryDatabaseClient


def _store() -> ModelHealthStore:
    return ModelHealthStore(failure_threshold=2, open_duration_ms=30)


def test_threshold_open_emits_event():
    store = _store()
    events = []
    store.add_state_observer(events.append)

    store.mark_failure("m1")
    assert events == []  # 未达阈值，无状态变更
    store.mark_failure("m1")
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, BreakerEvent)
    assert event.model_id == "m1"
    assert event.from_state == "closed" and event.to_state == "open"
    assert event.failure_count == 2  # 快照 = 触发计数
    assert event.create_time


def test_recovery_and_half_open_transitions():
    store = _store()
    events = []
    store.add_state_observer(events.append)

    store.mark_failure("m1")
    store.mark_failure("m1")  # → open
    time.sleep(0.05)  # 熔断窗口（30ms）过期
    permit = store.allow_call("m1")  # → half_open
    assert len(events) == 2
    assert events[1].from_state == "open" and events[1].to_state == "half_open"

    store.mark_success("m1")  # → closed（恢复事件）
    assert len(events) == 3
    assert events[2].from_state == "half_open" and events[2].to_state == "closed"

    # CLOSED 内成功重置计数：不是状态变更，无事件
    store.mark_success("m1")
    assert len(events) == 3


def test_probe_failure_reopens_with_zero_snapshot():
    store = _store()
    events = []
    store.add_state_observer(events.append)

    store.mark_failure("m1")
    store.mark_failure("m1")  # → open
    time.sleep(0.05)  # 熔断窗口（30ms）过期
    store.allow_call("m1")    # → half_open
    store.mark_failure("m1")  # 探测失败 → 立即重新熔断
    reopen = [e for e in events if e.from_state == "half_open" and e.to_state == "open"]
    assert len(reopen) == 1 and reopen[0].failure_count == 0  # 探测路径快照 0


def test_observer_exception_swallowed_and_counted():
    """观测 bug 永不打挂熔断器（D12 第一戒律）：异常吞掉 + 计数，状态机照常"""
    store = _store()

    def _broken(event):
        raise RuntimeError("observer bug")

    store.add_state_observer(_broken)
    store.mark_failure("m1")   # 未达阈值：无状态变更，无事件
    store.mark_failure("m1")   # 达阈值：CLOSED→open 一次转换
    assert store.observer_errors == 1
    assert store.health_by_id["m1"].state == HealthState.OPEN  # 熔断器本体不受影响

    store.mark_success("m1")   # open→closed 恢复转换
    assert store.health_by_id["m1"].state == HealthState.CLOSED
    assert store.observer_errors == 2


def test_snapshot_states_shape():
    store = _store()
    store.mark_failure("m1")
    store.mark_failure("m1")
    snapshot = store.snapshot_states()
    assert snapshot["states"] == {"m1": "open"}
    assert snapshot["observer_errors"] == 0


def test_hub_dispatches_breaker_event_without_breaking_call_events():
    """BreakerEvent 进环形缓冲 + 计数；同批 CallEvent 照常落库（分派白名单扩展）"""
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db))

    async def scenario():
        hub.emit(CallEvent(request_uid="r1", call_kind="chat", provider="p", model="m", status="success"))
        hub.emit(BreakerEvent(model_id="m9", from_state="closed", to_state="open",
                              failure_count=2, create_time="2026-10-11T12:00:00"))
        await hub.stop_and_drain()

    asyncio.run(scenario())
    assert len(db.select_rows(LLM_CALL_TABLE)) == 1  # CallEvent 照常落库
    assert hub.stats.breaker_events == 1
    assert len(hub.recent_breaker_events) == 1
    assert hub.recent_breaker_events[0].model_id == "m9"
    metrics = hub.metrics()
    assert metrics["observation"]["breaker_events"] == 1
    assert metrics["observation"]["recent_breaker_events"] == 1


def test_metrics_endpoint_includes_breakers_block():
    app = create_app(AppSettings(stack_profile="memory"))
    with TestClient(app) as client:
        container = client.app.state.container
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert "breakers" in body["data"]
        assert isinstance(body["data"]["breakers"]["stores"], list)
        # 观测未装配 LLM 栈时（memory profile 无 ai.yaml）stores 可为空列表
        _ = container.breaker_snapshot()
