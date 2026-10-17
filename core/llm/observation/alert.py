# -*- coding: utf-8 -*-
"""
core.llm.observation.alert - 告警通道（P10.4a T4.1，决策 D10/D13）

消息最小形态（D10 冻结契约；feishu/dingtalk/generic 三格式共用同一文本底稿）：
    级别(P1/P2) + 规则名 + 当前值 vs 阈值 + 维度(model/scene) + env 标记
    + 时间 + [已恢复] 标记 + 建议动作一行。不做值班排班、不做未确认升级重发。

投递纪律（D2「观测不反噬业务」的告警版）：
    - send() 只做消息→payload 的纯格式化 + 有界队列 put_nowait，调用方线程
      （含熔断事件落账进程）永不触发 IO；满即丢并计数；
    - 专职 daemon 发送线程串行同步 HTTP（httpx，带超时；返回非 2xx 抛错），
      失败吞掉计数、不重试（D10：不做未确认升级重发）；
    - 已接受并未停机的投递按 FIFO 全部交付；stop() 在停产后再 send 一律丢弃计数。
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, TYPE_CHECKING

import httpx

from core.llm.observation.alert_config import (
    FORMAT_DINGTALK,
    FORMAT_FEISHU,
    FORMAT_GENERIC,
    AlertConfig,
)

if TYPE_CHECKING:  # D12 冻结 schema（events 不反向依赖本模块，无环）；仅类型标注引用
    from core.llm.observation.events import BreakerEvent

# 消息级别（D10 默认分级：熔断 OPEN=P1、成功率越限=P1、预算 100%+=P1、
# 预算 80%=P2、丢弃超阈值=P2、估算占比=P2——规则按各自 D11 语义落在 T4.2/T4.3/T4.4）
LEVEL_P1 = "P1"
LEVEL_P2 = "P2"

@dataclass(frozen=True)
class AlertMessage:
    """一条告警（D10 最小形态字段；发射点构造后即冻结）

    Attributes:
        level: 级别 P1 | P2（级别语义归各规则，通道只透传）。
        rule: 规则名（如 breaker.open / breaker.recovered）。
        current: 当前值（人读文本）。
        threshold: 阈值（人读文本）。
        ts: ISO 时间（发射点物化——渲染发生在投递线程时刻不同没意义）。
        dimensions: 规则维度（model / scene / …）。
        env: 环境标记（来自配置；空省略）。
        recovered: [已恢复] 标记（恢复通知专用）。
        action: 建议动作一行（空省略该行）。
    """

    level: str
    rule: str
    current: str
    threshold: str
    ts: str
    dimensions: Mapping[str, str] = field(default_factory=dict)
    env: str = ""
    recovered: bool = False
    action: str = ""


def render_text(message: AlertMessage) -> str:
    """三格式共用文本底稿（feishu/dingtalk 的 text 内容 = generic 的 text 字段）"""
    head = f"[{message.level}]" + ("[已恢复]" if message.recovered else "") + f" {message.rule}"
    if message.env:
        head += f"（env={message.env}）"
    lines = [
        head,
        f"当前值: {message.current} | 阈值: {message.threshold}",
    ]
    if message.dimensions:
        lines.append("维度: " + " ".join(f"{k}={v}" for k, v in message.dimensions.items()))
    lines.append(f"时间: {message.ts}")
    if message.action:
        lines.append(f"建议动作: {message.action}")
    return "\n".join(lines)


def format_feishu(message: AlertMessage) -> Dict[str, Any]:
    """飞书自定义群机器人（D10：不上应用机器人，免鉴权与 token 续期）"""
    return {"msg_type": "text", "content": {"text": render_text(message)}}


def format_dingtalk(message: AlertMessage) -> Dict[str, Any]:
    """钉钉群机器人（msgtype=text）"""
    return {"msgtype": "text", "text": {"content": render_text(message)}}


def format_generic(message: AlertMessage) -> Dict[str, Any]:
    """通用 JSON：结构化全字段 + text 摘要（自建接收端用）"""
    return {
        "level": message.level,
        "rule": message.rule,
        "current": message.current,
        "threshold": message.threshold,
        "dimensions": dict(message.dimensions),
        "env": message.env,
        "ts": message.ts,
        "recovered": message.recovered,
        "action": message.action,
        "text": render_text(message),
    }


FORMATTERS: Dict[str, Callable[[AlertMessage], Dict[str, Any]]] = {
    FORMAT_FEISHU: format_feishu,
    FORMAT_DINGTALK: format_dingtalk,
    FORMAT_GENERIC: format_generic,
}


@dataclass
class AlertStats:
    """通道自健康计数（D13：经 alerter.stats_snapshot() 进 /metrics）

    「可观测的丢」（D2 同纪律）：任何一条未发出的告警都体现在某个计数器上。
    """

    accepted: int = 0          # 已入队（send 返回 True；最近触发时间在此记账）
    delivered: int = 0         # 发送线程交付成功（HTTP 2xx）
    send_errors: int = 0       # 投递失败（异常/非 2xx；吞掉计数，不重试）
    dropped_full: int = 0      # 队列满丢弃 + 停机后 send（背压可见）
    skipped_disabled: int = 0  # 通道未装配/未启用时的 send 调用
    last_sent_by_rule: Dict[str, str] = field(default_factory=dict)  # rule → 最近触发 ISO

    def snapshot(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "delivered": self.delivered,
            "send_errors": self.send_errors,
            "dropped_full": self.dropped_full,
            "skipped_disabled": self.skipped_disabled,
            "last_sent_by_rule": dict(self.last_sent_by_rule),
        }


class WebhookAlerter:
    """webhook 通道：send() 非阻塞入队 + 专职 daemon 线程串行投递

    Args:
        config: AlertConfig（yaml 装配；enabled 且 webhook_url 非空才真正投递）。
        sender: 注入的投递实现 (payload) -> None，None 用默认 httpx 同步 POST
                （T4.1 验收=注入桩断言 payload，不寒真 webhook）。
        maxsize: 有界队列上限（满即丢 + 计数，D2）。
        stats: 外部注入计数器；监控侧注入同实例即可在 /metrics 序列化。
    """

    def __init__(
        self,
        config: AlertConfig,
        *,
        sender: Optional[Callable[[Dict[str, Any]], None]] = None,
        maxsize: int = 100,
        stats: Optional[AlertStats] = None,
    ):
        self.config = config
        self.stats = stats if stats is not None else AlertStats()
        self._sender = sender
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._stopping = False

    # ==================== 发送侧（任意线程可调；绝不 IO） ====================

    def delivers(self) -> bool:
        """通道已真正装配（启用且具备投递目标——url 或注入 sender）"""
        return bool(self.config.enabled and (self.config.webhook_url or self._sender is not None))

    def send(self, message: AlertMessage) -> bool:
        """非阻塞投递：True=已入队（最近触发时间已记账）；False=未装配/满丢弃（均计数）"""
        if self._stopping:  # 停机后投递已不可能交付 → dropped_full（D2 停机语义对齐）
            with self._lock:
                self._maybe_drop_locked()
            return False
        if not self.delivers():
            with self._lock:
                self.stats.skipped_disabled += 1
            return False
        payload = FORMATTERS[self.config.fmt](message)
        try:
            self._queue.put_nowait((payload, message.rule))
        except queue.Full:
            with self._lock:
                self._maybe_drop_locked()
            return False
        with self._lock:
            self.stats.accepted += 1
            self.stats.last_sent_by_rule[message.rule] = message.ts  # D13 最近触发时间
        self._ensure_worker()
        return True

    def _maybe_drop_locked(self) -> None:
        self.stats.dropped_full += 1

    # ==================== 投递端（专职 daemon 线程，串行 HTTP） ====================

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._run, name="alert-webhook-sender", daemon=True
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            try:
                payload, rule = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stopping and self._queue.empty():
                    return
                continue
            self._deliver(rule, payload)

    def _deliver(self, rule: str, payload: Dict[str, Any]) -> None:
        try:
            (self._sender if self._sender is not None else self._default_sender())(payload)
        except Exception:  # noqa: BLE001 —— 告警失败只计数（不重试，D10），绝不反噬观测线程
            with self._lock:
                self.stats.send_errors += 1
        else:
            with self._lock:
                self.stats.delivered += 1

    def _default_sender(self) -> Callable[[Dict[str, Any]], None]:
        url = self.config.webhook_url
        headers = dict(self.config.headers)
        timeout = self.config.timeout_seconds

        def _post(payload: Dict[str, Any]) -> None:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()  # 非 2xx → send_errors 计数

        return _post

    # ==================== 停止与记账出口 ====================

    def stop(self, timeout: float = 2.0) -> None:
        """停产：已入队的告警交付完再退出（join 等待，超时放弃——daemon 兜底不悬挂进程）"""
        self._stopping = True
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)

    def stats_snapshot(self) -> Dict[str, Any]:
        """/metrics 序列化出口（D13；含最近触发时间）"""
        with self._lock:
            return self.stats.snapshot()


# ===================== 熔断告警消费者（P10.4a T4.2，D11①） =====================

BREAKER_OPEN_RULE = "breaker.open"
BREAKER_RECOVERED_RULE = "breaker.recovered"

# health_store 状态字面量（D12 已随 BreakerEvent schema 冻结：closed/open/half_open）
BREAKER_STATE_OPEN = "open"


class BreakerAlertConsumer:
    """熔断事件 → 告警（T4.2；消费 hub flush 管线的 BreakerEvent 白名单分支）

    语义（D11① 冻结）：熔断 OPEN 事件驱动即发 [P1]；同模型冷却 silence_seconds
    （默认 600s=10min；冷却自最近一次 OPEN 告警起算、恢复不重置——熔断-半开振荡
    刷屏靠它）；状态回复 closed 即发恢复通知 [P2][已恢复]，但只对曾报出 OPEN 的
    模型闭环（冷却拦截的 OPEN 无操作员可见事故，恢复通知跟着免了）。

    调用纪律（D2/D12）：on_breaker_event 在 hub 落账线程同步调用——本方法自身
    全程无 IO（send 只做格式化 + put_nowait）；hub 侧另有兜底吞错 + 计数
    （breaker_alert_errors 进 /metrics，观测 bug 永不打挂熔断器账本）。
    """

    def __init__(self, alerter: WebhookAlerter, *, clock: Callable[[], float] = time.monotonic):
        self._alerter = alerter
        self._clock = clock
        self._silence_seconds = alerter.config.silence_seconds  # 与 D10 静默期同一配置源
        self._open_alerted_at: Dict[str, float] = {}   # model → 最近 OPEN 告警（clock 时刻）
        self._open_pending: Dict[str, str] = {}        # model → 已报 OPEN 的 ISO（恢复闭环用）
        self._lock = threading.Lock()
        # 语义计数（D2「可观测的丢」+ D13 最近触发；快照随 consumer.metrics() 进 /metrics）
        self.counters: Dict[str, int] = {
            "seen": 0,             # 消费的 BreakerEvent 数
            "opened_alerted": 0,   # OPEN 告警发出（入队成功）
            "opened_suppressed": 0,  # 冷却期内被抑制的 OPEN
            "recovered_sent": 0,   # 恢复通知发出
            "recovered_suppressed": 0,  # 无闭环可挂（此前 OPEN 未报出）的恢复
            "send_dropped": 0,     # OPEN/恢复因通道满载/停机未入队（冷却不记账，下个事件重试）
        }

    # ==================== 消费入口（hub 白名单分支调用；无 IO） ====================

    def on_breaker_event(self, event: "BreakerEvent") -> None:
        with self._lock:
            self.counters["seen"] += 1
            if not self._alerter.delivers():
                return
            if event.to_state == BREAKER_STATE_OPEN:
                self._handle_open(event)
            elif (
                event.to_state == "closed"
                and event.from_state in (BREAKER_STATE_OPEN, "half_open")
            ):
                self._handle_recovery(event)

    def _handle_open(self, event: "BreakerEvent") -> None:
        now = self._clock()
        last = self._open_alerted_at.get(event.model_id)
        if last is not None and (now - last) < self._silence_seconds:
            self.counters["opened_suppressed"] += 1  # D11①：同模型冷却不重复发
            return
        message = AlertMessage(
            level=LEVEL_P1,
            rule=BREAKER_OPEN_RULE,
            current=f"连续失败 {event.failure_count} 次，熔断器打开",
            threshold="连续失败达熔断阈值（failover 自动切流）",
            ts=event.create_time,
            dimensions={"model": event.model_id},
            env=self._alerter.config.env,
            action="检查模型上游可用性；探测通过后自动发送恢复通知",
        )
        if self._alerter.send(message):
            self.counters["opened_alerted"] += 1
            self._open_alerted_at[event.model_id] = now
            self._open_pending[event.model_id] = event.create_time  # 恢复闭环登记
        else:
            self.counters["send_dropped"] += 1  # 未入队不占冷却（下个事件即重试）

    def _handle_recovery(self, event: "BreakerEvent") -> None:
        pending = self._open_pending.pop(event.model_id, None)
        if pending is None:
            self.counters["recovered_suppressed"] += 1  # 没报过 OPEN → 恢复也免（无闭环）
            return
        message = AlertMessage(
            level=LEVEL_P2,
            rule=BREAKER_RECOVERED_RULE,
            current="熔断器已恢复（探测通过）",
            threshold="state=closed",
            ts=event.create_time,
            dimensions={"model": event.model_id},
            env=self._alerter.config.env,
            recovered=True,  # D10 [已恢复] 标记
            action="无需处置",
        )
        if self._alerter.send(message):
            self.counters["recovered_sent"] += 1
        else:
            self._open_pending[event.model_id] = pending  # 闭环账本不丢（下个恢复重试）
            self.counters["send_dropped"] += 1

    # ==================== /metrics 出口（D13） ====================

    def metrics(self) -> Dict[str, Any]:
        """/metrics 告警区块：语义计数 + 通道自健康（含最近触发时间）"""
        with self._lock:
            return {
                "enabled": self._alerter.delivers(),
                "silence_seconds": self._silence_seconds,
                "counters": dict(self.counters),
                "open_pending": dict(self._open_pending),  # 当前报 OPEN 未闭环的模型
                "alerter": self._alerter.stats_snapshot(),
            }
