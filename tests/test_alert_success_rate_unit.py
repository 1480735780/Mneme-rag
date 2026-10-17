# -*- coding: utf-8 -*-
"""P10.4b T4.3 单测：成功率告警规则（D11②）+ hub CallEvent 分派 + wiring 装配

覆盖：
    - 窗口语义：5min 窗 ≥20 请求且失败率 >20% 才告警（P1）；样本不足/阈值未过不告警
    - 分母剔除：canceled 不进窗口（D4）；is_final=0（attempt 级）不进窗口但喂连败
    - 冷却 30min：命中后同模型抑制，滚过后仍在恶化再发
    - 快速通道：连续 5 次全败即时告警（不等窗口）；第 6+ 次不重复；成功/取消重置
    - hub 分派：CallEvent 照常落库 + 规则被调 + 规则 bug 吞掉计数（call_alert_errors）
    - metrics：observation.alerting {breaker, success_rate} 双区块
    - wiring：enabled yaml → 双消费者装配且成功事件可流到 sender 桩
"""
import asyncio
from pathlib import Path

from app.config import AppSettings
from app.wiring import AppContainer
from core.llm.observation import LLM_CALL_TABLE, CallEvent, LlmCallDao, ObservationHub
from core.llm.observation.alert import WebhookAlerter
from core.llm.observation.alert_config import AlertConfig
from core.llm.observation.alert_rules import (
    FAST_PATH_STREAK,
    SuccessRateAlertRule,
)
from core.llm.observation.events import STATUS_FAILED, STATUS_SUCCESS
from storage.database import InMemoryDatabaseClient


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _alist(**overrides) -> AlertConfig:
    fields = dict(
        enabled=True,
        env="dev",
        fmt="feishu",
        webhook_url="https://hook.test",
        headers={},
        timeout_seconds=2.0,
        silence_seconds=600.0,
    )
    fields.update(overrides)
    return AlertConfig(**fields)


def _rule(alerter=None, clock=None) -> SuccessRateAlertRule:
    alerter = alerter if alerter is not None else WebhookAlerter(_alist(), sender=lambda _p: None)
    return SuccessRateAlertRule(alerter, clock=clock if clock is not None else _FakeClock())


def _event(model: str = "m1", status: str = STATUS_SUCCESS, *, is_final: bool = True,
           attempt_no: int = 1, ts: str = "2026-10-17T10:00:00") -> CallEvent:
    return CallEvent(
        request_uid=f"r-{model}-{attempt_no}-{ts}",
        call_kind="chat", provider="p", model=model, status=status,
        is_final=is_final, attempt_no=attempt_no, create_time=ts,
    )


# ==================== 窗口语义（D11②） ====================


def test_window_below_sample_floor_no_alert():
    rule = _rule()
    # 10 成功 + 5 attempt 失败 = 15 请求 <20 → 样本不足，不告警；
    # attempt 失败只喂连败/不进窗口（5 连败快速通道照发，属另一条规则的独立语义）
    for i in range(10):
        rule.on_call_event(_event(ts=f"s{i}"))
    for i in range(5):
        rule.on_call_event(_event(status=STATUS_FAILED, is_final=False, ts=f"f{i}"))
    assert rule.counters["window_alerts"] == 0
    assert rule.counters["fastpath_alerts"] == 1
    assert rule.counters["window_samples"] == 10  # attempt 级不进窗口


def test_window_rate_not_over_threshold_no_alert():
    rule = _rule()
    for i in range(20):  # 20 请求全成功 → 0%
        rule.on_call_event(_event(ts=f"s{i}"))
    assert rule.counters["window_alerts"] == 0
    rule.on_call_event(_event(status=STATUS_FAILED, ts="f0"))  # 1/21 = 4.8%
    assert rule.counters["window_alerts"] == 0


def test_window_over_threshold_alerts_with_p1_and_dims():
    """窗口告警形态（D10 全字段）；连败穿插成功避免快速通道参与，隔离两条规则"""
    sent: list = []
    alerter = WebhookAlerter(_alist(), sender=sent.append)
    rule = _rule(alerter=alerter)
    # 24 请求 5 失败（每次失败间隔 ≥3 成功 → 连败恒 <5，快通道全静默）
    pattern = (
        ["s"] * 4 + ["f"] + ["s"] * 4 + ["f"] + ["s"] * 4 + ["f"]
        + ["s"] * 4 + ["f"] + ["s"] * 3 + ["f"]
    )  # 19 成功 + 5 失败 = 24
    ts_index = 0
    for kind in pattern:
        status = STATUS_SUCCESS if kind == "s" else STATUS_FAILED
        rule.on_call_event(_event(status=status, ts=f"e{ts_index}"))
        ts_index += 1
    assert rule.counters["fastpath_alerts"] == 0
    assert rule.counters["window_alerts"] == 1  # 5/24 ≈ 20.8% > 20%
    alerter.stop(timeout=2.0)
    assert len(sent) == 1
    text = sent[0]["content"]["text"]
    assert "[P1] success_rate.window（env=dev）" in text
    assert "5min 失败 5/24" in text
    assert "model=m1" in text
    # D13 通道记账
    assert alerter.stats_snapshot()["last_sent_by_rule"] == {
        "success_rate.window": "e23"
    }


def test_window_is_strictly_over_rate():
    """>20% 严格大于：恰好 20% 不告警（4 失败 / 20 请求）"""
    rule = _rule()
    for i in range(16):
        rule.on_call_event(_event(ts=f"s{i}"))
    for i in range(4):
        rule.on_call_event(_event(status=STATUS_FAILED, ts=f"f{i}"))  # 4/20 = 20%
    assert rule.counters["window_alerts"] == 0


# ==================== 分母剔除（D4） ====================


def test_canceled_never_enters_window():
    rule = _rule()
    for i in range(20):
        rule.on_call_event(_event(ts=f"s{i}"))
    rule.on_call_event(_event(status="canceled", ts="c0"))  # canceled：分母剔除
    rule.on_call_event(_event(status=STATUS_FAILED, ts="f0"))  # 1/21 = 4.8%
    assert rule.counters["window_alerts"] == 0
    assert rule.counters["window_samples"] == 21  # 只记成功+失败，canceled 未计


def test_attempts_do_not_enter_window_but_feed_streak():
    """is_final=0（attempt 级）不进窗口分母；全败 attempt 喂快速通道"""
    sent: list = []
    alerter = WebhookAlerter(_alist(), sender=sent.append)
    rule = _rule(alerter=alerter)
    for i in range(4):
        rule.on_call_event(_event(status=STATUS_FAILED, is_final=False, attempt_no=i + 1,
                                  ts=f"a{i}"))  # 连败 1..4 不告警
    assert rule.counters["window_samples"] == 0
    assert rule.counters["fastpath_alerts"] == 0
    rule.on_call_event(_event(status=STATUS_FAILED, is_final=False, attempt_no=5, ts="a4"))
    assert rule.counters["fastpath_alerts"] == 1     # 第 5 连败 → 快速通道
    assert rule.counters["window_alerts"] == 0       # 请求级窗口零样本：依旧静止
    alerter.stop(timeout=2.0)
    assert len(sent) == 1


# ==================== 冷却 30min（D11②） ====================


def test_window_cooldown_thirty_minutes():
    """冷却 30min：首告警 → 冷却内再越限仅压制 → 冷却滚过仍在恶化再发

    FakeClock 逐事件推进（同 epoch 样本会被 5min 剪除清空，需真实时间轴）。
    """
    clock = _FakeClock()
    rule = _rule(clock=clock)
    for i in range(19):
        rule.on_call_event(_event(ts=f"s{i}"))
        clock.advance(1)                                                    # t=1..19
    for i in range(4):
        rule.on_call_event(_event(status=STATUS_FAILED, ts=f"f{i}"))
        clock.advance(1)                                                    # t=20..23 前
    rule.on_call_event(_event(status=STATUS_FAILED, ts="f4"))  # t=23：24 样本 20.8% 首告警
    assert rule.counters["window_alerts"] == 1

    clock.advance(377)   # t=400：距首告警 377s（仍在 30min 冷却内）
    for _ in range(16):
        rule.on_call_event(_event(ts="ns"))                                 # 新窗口垫 16 成功
    for _ in range(4):
        rule.on_call_event(_event(status=STATUS_FAILED, ts="nf"))           # 4/20=20% 未越限
    rule.on_call_event(_event(status=STATUS_FAILED, ts="nf"))   # 5/21=23.8% 越限 → 压制
    assert rule.counters["window_alerts"] == 1
    assert rule.counters["window_suppressed"] == 1

    clock.advance(1500)  # t=1900：距首告警 1877s ≥ 1800（冷却滚过）
    for _ in range(16):
        rule.on_call_event(_event(ts="ws"))
    for _ in range(4):
        rule.on_call_event(_event(status=STATUS_FAILED, ts="wf"))           # 4/20=20% 未越限
    rule.on_call_event(_event(status=STATUS_FAILED, ts="wf"))   # 5/21 越限 → 再告警
    assert rule.counters["window_alerts"] == 2
    assert rule.counters["window_suppressed"] == 1  # 滚过后不再产生压制计数


def test_window_cooldown_is_per_model():
    clock = _FakeClock()
    rule = _rule(clock=clock)
    for i in range(19):
        rule.on_call_event(_event(model="a", ts=f"s{i}"))
    for i in range(5):
        rule.on_call_event(_event(model="a", status=STATUS_FAILED, ts=f"f{i}"))  # a 告警
    assert rule.counters["window_alerts"] == 1
    for i in range(19):
        rule.on_call_event(_event(model="b", ts=f"sb{i}"))
    for i in range(5):
        rule.on_call_event(_event(model="b", status=STATUS_FAILED, ts=f"fb{i}"))  # b 独立账
    assert rule.counters["window_alerts"] == 2
    assert rule.counters["window_suppressed"] == 0


# ==================== 快速通道（D11②） ====================


def test_fastpath_exact_streak_alerts_once_per_episode():
    rule = _rule()
    for i in range(FAST_PATH_STREAK):
        rule.on_call_event(_event(status=STATUS_FAILED, is_final=False, attempt_no=i + 1,
                                  ts=f"a{i}"))
    assert rule.counters["fastpath_alerts"] == 1
    for i in range(3):  # 第 6..8 次连败不再重复发
        rule.on_call_event(_event(status=STATUS_FAILED, ts=f"a5+i{i}"))
    assert rule.counters["fastpath_alerts"] == 1


def test_success_and_canceled_reset_streak():
    """成功/取消重置连败；连败属于 attempt 级（final 与否同样计入）"""
    rule = _rule()
    for i in range(4):
        rule.on_call_event(_event(status=STATUS_FAILED, ts=f"f{i}"))
    rule.on_call_event(_event(status="canceled", ts="c0"))  # 取消中断连败（D4 语义延伸）
    for i in range(4):
        rule.on_call_event(_event(status=STATUS_FAILED, ts=f"fb{i}"))
    rule.on_call_event(_event(status=STATUS_FAILED, ts="g0"))  # 第 5 连败 → 快速通道
    assert rule.counters["fastpath_alerts"] == 1
    rule.on_call_event(_event(status=STATUS_SUCCESS, ts="s0"))  # 成功重置
    for i in range(4):  # 重置后再 4 连败：不再凑满 → 不重复发
        rule.on_call_event(_event(status=STATUS_FAILED, ts=f"g1{i}"))
    assert rule.counters["fastpath_alerts"] == 1


def test_disabled_channel_rule_noops_but_counts_seen():
    rule = _rule(alerter=WebhookAlerter(AlertConfig()))
    rule.on_call_event(_event(status=STATUS_FAILED))
    assert rule.counters["seen"] == 1
    assert rule.counters["fastpath_alerts"] == 0


# ==================== hub 分派白名单（T4.3 接缝） ====================


def test_hub_invokes_success_rate_rule_and_lands_rows():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    rule = _rule()
    hub = ObservationHub(LlmCallDao(db), call_alert=rule)

    async def scenario():
        hub.emit(_event(status=STATUS_SUCCESS, ts="t0"))
        hub.emit(_event(status=STATUS_FAILED, ts="t1"))
        await hub.stop_and_drain()

    asyncio.run(scenario())
    rows = db.select_rows(LLM_CALL_TABLE)
    assert len(rows) == 2 and rows[1]["status"] == "failed"
    assert rule.counters["seen"] == 2 and rule.counters["window_samples"] == 2
    metrics = hub.metrics()["observation"]
    assert metrics["alerting"]["success_rate"]["counters"]["seen"] == 2


def test_hub_swallows_rule_errors_without_breaking_rows():
    """成功率规则 bug 吞掉 + 计数（call_alert_errors）；CallEvent 落库不受影响"""

    class _BurstRule:
        def on_call_event(self, event):
            raise RuntimeError("rule bug")

    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), call_alert=_BurstRule())

    async def scenario():
        hub.emit(_event(status=STATUS_SUCCESS, ts="t0"))
        await hub.stop_and_drain()

    asyncio.run(scenario())
    assert hub.stats.call_alert_errors == 1
    assert len(db.select_rows(LLM_CALL_TABLE)) == 1


# ==================== wiring 装配 ====================


def test_wiring_builds_both_consumers_when_enabled(tmp_path):
    cfg_path = tmp_path / "alerts.yaml"
    cfg_path.write_text(
        "alerts:\n  enabled: true\n  env: prod\n  format: generic\n"
        "  webhook_url: 'https://hook.test'\n",
        encoding="utf-8",
    )
    container = AppContainer._build_memory(AppSettings(alerts_yaml_path=str(cfg_path)))
    try:
        hub = container.observation_hub
        assert hub.breaker_alert is not None and hub.call_alert is not None
        # 共用同一 alerter（单一通道出口）
        assert (
            hub.breaker_alert._alerter is hub.call_alert._alerter is container.webhook_alerter
        )
    finally:
        asyncio.run(container.aclose())


def test_wiring_no_alert_yaml_disables_consumers(tmp_path):
    missing = tmp_path / "absent.yaml"
    container = AppContainer._build_memory(AppSettings(alerts_yaml_path=str(missing)))
    try:
        hub = container.observation_hub
        assert hub.breaker_alert is None and hub.call_alert is None
    finally:
        asyncio.run(container.aclose())
