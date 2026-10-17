# -*- coding: utf-8 -*-
"""P10.4a T4.2 单测：熔断告警消费者（D11①）+ hub 分派白名单 + wiring 装配

覆盖：
    - OPEN 告警：事件驱动即发 [P1]，D10 全字段（模型维度/env/时间/建议动作）；
    - 同模型冷却：silence_seconds 内重复 OPEN 抑制、跨模型互不影响、
      冷却过后再次 OPEN 照发（D11①：同模型 10min 字面语义）；
    - 恢复通知：仅对曾报出 OPEN 的模型闭环（[P2][已恢复]），闭环账本弹出；
      冷却拦截的 OPEN → 恢复也免（无操作员可见事故，通知跟着免）；
    - 恢复不重置冷却（熔断-半开振荡不刷屏）；
    - 通道未入队（满载）不占冷却、不丢恢复闭环账本（下个事件重试）；
    - hub 分派：消费者被调、异常吞掉并计数 breaker_alert_errors（落账与 CallEvent 不受影响）、
      无消费者缺省不装配（T1.7 回归不变）；/metrics observation.alerting 区块；
    - wiring：出厂样例（enabled=false）→ 不装配；enabled+url yaml → 装配并把
      BreakerEvent 一路送到 sender 桩；非法 yaml → 显著降级不阻断启动。
"""
import asyncio
from pathlib import Path

from app.config import AppSettings
from app.wiring import AppContainer
from core.llm.observation import LLM_CALL_TABLE, CallEvent, LlmCallDao, ObservationHub
from core.llm.observation.alert import BreakerAlertConsumer, WebhookAlerter
from core.llm.observation.alert_config import AlertConfig, load_alert_from_dict
from core.llm.observation.events import BreakerEvent
from storage.database import InMemoryDatabaseClient

_ROOT = Path(__file__).resolve().parent.parent


class _FakeClock:
    """注入时钟：advance 推进（冷却语义判定用，免真睡眠）"""

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
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/x",
        headers={},
        timeout_seconds=2.0,
        silence_seconds=600.0,
    )
    fields.update(overrides)
    return AlertConfig(**fields)


def _event(model: str = "m1", to_state: str = "open", from_state: str = "closed",
           failures: int = 3, ts: str = "2026-10-17T10:00:00") -> BreakerEvent:
    return BreakerEvent(
        model_id=model, from_state=from_state, to_state=to_state,
        failure_count=failures, create_time=ts,
    )


def _consumer(alerter=None, silence: float = 600.0):
    """（消费者, 桩投递列表, FakeClock）三件套；默认可投递通道"""
    clock = _FakeClock()
    actual = alerter if alerter is not None else WebhookAlerter(
        _alist(silence_seconds=silence), sender=lambda payload: None
    )
    return BreakerAlertConsumer(actual, clock=clock), actual, clock


# ==================== 消费者语义（D11①） ====================


def test_open_alert_dispatched_with_d10_fields():
    consumer, alerter, _ = _consumer()
    consumer.on_breaker_event(_event(failures=3, ts="2026-10-17T10:00:00"))

    counters = consumer.counters
    assert counters["seen"] == 1 and counters["opened_alerted"] == 1
    stats = alerter.stats_snapshot()
    assert stats["accepted"] == 1
    assert stats["last_sent_by_rule"] == {"breaker.open": "2026-10-17T10:00:00"}  # D13
    # 冷却账本登记：同模型下一发受冷却约束 / 恢复闭环可查
    assert "m1" in consumer.metrics()["open_pending"]


def test_same_model_within_cooldown_suppressed():
    consumer, _, clock = _consumer()
    consumer.on_breaker_event(_event(ts="t0"))     # 首发
    clock.advance(300)
    consumer.on_breaker_event(_event(ts="t+300"))  # 冷却期内（600s）→ 抑制
    assert consumer.counters["opened_alerted"] == 1
    assert consumer.counters["opened_suppressed"] == 1

    clock.advance(301)  # 距首发 601s → 过冷却
    consumer.on_breaker_event(_event(ts="t+601", failures=4))
    assert consumer.counters["opened_alerted"] == 2
    assert consumer.counters["opened_suppressed"] == 1


def test_cooldown_is_per_model():
    """同模型冷却 ≠ 全局静默：另一模型首 OPEN 随时照发"""
    consumer, _, clock = _consumer()
    consumer.on_breaker_event(_event(model="a", ts="t0"))
    clock.advance(60)
    consumer.on_breaker_event(_event(model="b", ts="t+60"))
    stats = consumer.metrics()
    assert stats["counters"]["opened_alerted"] == 2
    assert set(stats["open_pending"]) == {"a", "b"}


def test_recovery_notice_closes_loop():
    """曾报 OPEN 的模型恢复 → [P2][已恢复] 通知 + 闭环账本弹出"""
    sent: list = []
    alerter = WebhookAlerter(_alist(), sender=sent.append)
    consumer, _, _ = _consumer(alerter=alerter)

    consumer.on_breaker_event(_event(ts="t0"))                       # OPEN [P1]
    consumer.on_breaker_event(_event(to_state="half_open", from_state="open", ts="t1"))
    # half_open 不是告警点（D11 只挂 OPEN + 恢复）
    assert consumer.counters["seen"] == 2 and alerter.stats_snapshot()["accepted"] == 1

    consumer.on_breaker_event(_event(to_state="closed", from_state="half_open", ts="t2"))
    alerter.stop(timeout=2.0)  # 停机前把已入队消息交付完（对齐异步投递线程）
    assert consumer.counters["recovered_sent"] == 1
    assert consumer.metrics()["counters"]["seen"] == 3
    assert len(sent) == 2
    # 恢复载荷形态（feishu text）：[P2][已恢复] breaker.recovered + 模型维度
    text = sent[1]["content"]["text"]
    assert "[P2][已恢复] breaker.recovered" in text
    assert "model=m1" in text
    assert "无需处置" in text
    # 闭环弹出：/metrics 不再挂 pending
    assert consumer.metrics()["open_pending"] == {}


def test_recovery_suppressed_without_prior_alert():
    """闭环重开后被冷却拦截 → 该次恢复无账可挂（recovered_suppressed）"""
    consumer, alerter, clock = _consumer()
    consumer.on_breaker_event(_event(ts="t0"))                        # OPEN 告警（登记闭环）
    clock.advance(50)
    consumer.on_breaker_event(_event(to_state="closed", from_state="open", ts="t+50"))
    assert consumer.counters["recovered_sent"] == 1                   # 有闭环 → 通知
    clock.advance(50)
    consumer.on_breaker_event(_event(ts="t+100", failures=4))         # 距首发 100s → 冷却拦截
    assert consumer.counters["opened_suppressed"] == 1
    clock.advance(50)
    consumer.on_breaker_event(_event(to_state="closed", from_state="open", ts="t+150"))
    # 拦截的 OPEN 没登记闭环 → 恢复通知跟着免（无操作员可见事故）
    assert consumer.counters["recovered_suppressed"] == 1
    assert alerter.stats_snapshot()["accepted"] == 2                  # 仅 OPEN + 首次恢复


def test_recovery_does_not_reset_open_cooldown():
    """恢复不重置冷却（D11① 字面语义）：恢复后 600s 内重开被抑制，防振荡刷屏"""
    consumer, _, clock = _consumer()
    consumer.on_breaker_event(_event(ts="t0"))                       # OPEN 告警
    clock.advance(60)
    consumer.on_breaker_event(_event(to_state="closed", from_state="open", ts="t+60"))     # 恢复
    clock.advance(300)
    consumer.on_breaker_event(_event(ts="t+360"))                    # 距首发 360s → 仍冷却
    counters = consumer.counters
    assert counters["opened_alerted"] == 1 and counters["opened_suppressed"] == 1
    clock.advance(300)  # t=660 → 过冷却
    consumer.on_breaker_event(_event(ts="t+660", failures=5))
    assert counters["opened_alerted"] == 2


def test_dropped_send_does_not_consume_cooldown_or_recovery_book():
    """满载/停机未入队：冷却不记账（下个事件可重试）、恢复闭环账本不丢"""

    class _DroppingAlerter(WebhookAlerter):
        """总是拒绝入队的通道桩（模拟满载/未装配时的 send 返回 False）"""

        def send(self, message):  # noqa: D102 —— 直接短路 False
            return False

    consumer, alerter, _ = _consumer(alerter=_DroppingAlerter(_alist()))
    consumer.on_breaker_event(_event(ts="t0"))
    assert consumer.counters["send_dropped"] == 1
    assert consumer.counters["opened_alerted"] == 0
    assert consumer.metrics()["open_pending"] == {}  # 冷却和闭环都未记账

    consumer.on_breaker_event(_event(to_state="closed", from_state="open", ts="t1"))
    # 无闭环（没报出 OPEN）→ recovered_suppressed
    assert consumer.counters["recovered_suppressed"] == 1


def test_disabled_alerter_consumer_noops():
    """通道未启用（样例 enabled=false 也可配出）：消费即返回，不做静默记账"""
    consumer, _, _ = _consumer(alerter=WebhookAlerter(AlertConfig()))
    consumer.on_breaker_event(_event())
    metrics = consumer.metrics()
    assert metrics["enabled"] is False
    assert metrics["counters"]["seen"] == 1


# ==================== hub 分派白名单（T4.2 接缝） ====================


def test_hub_invokes_consumer_and_metrics_expose_alerting():
    """BreakerEvent 分支消费 + alerting 区块；同批 CallEvent 照常落库"""
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    sent: list = []
    alerter = WebhookAlerter(
        load_alert_from_dict({
            "alerts": {"enabled": True, "env": "dev", "format": "generic",
                       "webhook_url": "https://in"}
        }),
        sender=sent.append,
    )
    consumer = BreakerAlertConsumer(alerter)
    hub = ObservationHub(LlmCallDao(db), breaker_alert=consumer)

    async def scenario():
        hub.emit(CallEvent(request_uid="r1", call_kind="chat", provider="p", model="m",
                           status="success"))
        hub.emit(_event(model="m9"))
        await hub.stop_and_drain()

    asyncio.run(scenario())
    assert len(db.select_rows(LLM_CALL_TABLE)) == 1
    assert db.select_rows(LLM_CALL_TABLE)[0]["call_kind"] == "chat"
    assert hub.stats.breaker_events == 1
    assert hub.stats.breaker_alert_errors == 0
    metrics = hub.metrics()["observation"]
    assert metrics["alerting"]["breaker"]["counters"]["seen"] == 1
    assert metrics["alerting"]["breaker"]["counters"]["opened_alerted"] == 1
    # generic 格式 → 结构化全字段（含 D13 通道记账）
    assert sent[0]["rule"] == "breaker.open" and sent[0]["env"] == "dev"
    assert metrics["alerting"]["breaker"]["alerter"]["accepted"] == 1


def test_hub_swallows_consumer_errors_and_keeps_batch():
    """消费者 bug 吞掉 + 计数（D12 同戒律）：环形缓冲/CallEvent 落库不受影响"""

    class _BurstConsumer:
        def on_breaker_event(self, event):
            raise RuntimeError("consumer bug")

    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    hub = ObservationHub(LlmCallDao(db), breaker_alert=_BurstConsumer())

    async def scenario():
        hub.emit(CallEvent(request_uid="r2", call_kind="embedding", provider="p", model="m",
                           status="success"))
        hub.emit(_event(model="m8"))
        await hub.stop_and_drain()

    asyncio.run(scenario())
    assert hub.stats.breaker_alert_errors == 1
    assert len(hub.recent_breaker_events) == 1  # 缓冲照常
    assert len(db.select_rows(LLM_CALL_TABLE)) == 1  # 落账照常
    assert hub.metrics()["observation"]["breaker_alert_errors"] == 1


def test_hub_without_consumer_unchanged():
    """缺省 None：不接告警，T1.7 行为分毫不变（回归护栏）"""
    hub = ObservationHub(LlmCallDao(InMemoryDatabaseClient({LLM_CALL_TABLE: []})))
    assert hub.breaker_alert is None
    metrics = hub.metrics()
    assert "alerting" not in metrics["observation"]


# ==================== wiring 装配（app/wiring.py） ====================


def test_wiring_memory_container_disables_alert_by_sample():
    """出厂样例 enabled=false → 读到配置但通道不装配（hub.breaker_alert is None）"""
    container = AppContainer._build_memory(AppSettings())
    try:
        assert container.alert_config is not None  # 出厂样例可解析
        assert container.alert_config.enabled is False
        assert container.webhook_alerter is None
        assert container.observation_hub.breaker_alert is None
    finally:
        asyncio.run(container.aclose())


def test_wiring_builds_consumer_when_enabled(tmp_path):
    cfg_path = tmp_path / "alerts.yaml"
    cfg_path.write_text(
        "alerts:\n"
        "  enabled: true\n"
        "  env: prod\n"
        "  format: generic\n"
        "  webhook_url: 'https://hook.test'\n",
        encoding="utf-8",
    )
    container = AppContainer._build_memory(
        AppSettings(alerts_yaml_path=str(cfg_path))
    )
    try:
        assert container.observation_hub.breaker_alert is not None
        assert container.webhook_alerter.delivers() is True
    finally:
        asyncio.run(container.aclose())
    # aclose 后停产令牌生效：再 send 走 dropped_full（不悬挂线程）
    assert container.webhook_alerter._stopping is True


def test_wiring_invalid_alerts_yaml_degrades(tmp_path):
    """非法 yaml（未知键=拼写错）→ 显著降级：不装配、不阻断启动"""
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("alerts:\n  webhoook_url: 'https://x'\n", encoding="utf-8")
    container = AppContainer._build_memory(AppSettings(alerts_yaml_path=str(cfg_path)))
    try:
        assert container.alert_config is None
        assert container.observation_hub.breaker_alert is None
    finally:
        asyncio.run(container.aclose())


def test_settings_default_alerts_path():
    assert AppSettings.from_env().alerts_yaml_path == "resources/alerts.yaml"
