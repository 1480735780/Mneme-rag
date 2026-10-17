# -*- coding: utf-8 -*-
"""
core.llm.observation.alert_rules - 告警规则（P10.4b 起；事件驱动内存态，复用 P10.4a 通道）

T4.3（D11②）：成功率告警——
    - 请求级滑窗：5min 窗口 ≥20 次且失败率 >20% → [P1]（冷却 30min，自该模型上次
      窗口告警起算；冷却命中窗口继续恶化不重复发，冷却滚过后仍在恶化再发）；
    - attempt 级快速通道：同一模型连续 5 次全败即时告警（不等窗口填满）——
      以「凑满连败」为天然节流（第 6、7 次连败不再发），成功/取消中断连败；
    - canceled 剔除分母（D4：不进失败率），同样不喂连败（严格 consecutive 语义）。

两级口径（D4）：attempt 级（每次真实 API 调用，含重试）喂快速通道；请求级
（is_final 最后一跳）喂窗口。事件驱动内存态：进程重启窗口冷启动（重新积累
≥20 请求再武装）——重启空档不伪造计数，属「可观测的丢」的接受面。

调用纪律（D2/D12 同戒律）：on_call_event 在 hub 落账线程同步调用、全程无 IO；
hub 侧兜底吞错 + 计数（call_alert_errors 进 /metrics）。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, Tuple

from core.llm.observation.alert import AlertMessage, LEVEL_P1, WebhookAlerter
from core.llm.observation.events import (
    STATUS_CANCELED,
    STATUS_FAILED,
)

if TYPE_CHECKING:  # events 不反向依赖本模块，无环
    from core.llm.observation.events import CallEvent

# ===================== 常量（D11② 字面语义） =====================

WINDOW_SECONDS = 300.0          # 5min 滑窗
WINDOW_MIN_REQUESTS = 20        # 最小样本（请求级）
WINDOW_FAIL_RATE = 0.20         # 失败率阈值（严格大于）
FAST_PATH_STREAK = 5            # 连续 5 次全败快速通道
WINDOW_SILENCE_SECONDS = 1800.0  # 窗口告警冷却 30min

RULE_WINDOW = "success_rate.window"
RULE_FAST_PATH = "success_rate.streak"


def _event_ts(event_ts: str | None) -> str:
    """告警时间戳：优先发射点物化的 create_time，缺省兜底当前 UTC ISO"""
    if event_ts:
        return event_ts
    return datetime.now(timezone.utc).isoformat()


class SuccessRateAlertRule:
    """成功率告警规则（T4.3）：滑窗 + 快速通道，per-model 管辖

    Args:
        alerter: 与熔断告警共用同一 WebhookAlerter（同一告警通道出口）。
        clock: 注入时钟（默认 time.monotonic；测试用 FakeClock 推进冷却）。
    """

    def __init__(self, alerter: WebhookAlerter, *, clock: Callable[[], float] = time.monotonic):
        self._alerter = alerter
        self._clock = clock
        self._windows: Dict[str, deque] = {}                    # model → (epoch, failed)
        self._streaks: Dict[str, int] = {}                      # model → 连续全败计数
        self._last_window_alert: Dict[str, float] = {}          # model → 上次窗口告警 epoch
        self._lock = threading.Lock()
        # 语义计数（D2「可观测的丢」+ D13；随 metrics() 进 /metrics）
        self.counters: Dict[str, int] = {
            "seen": 0,               # 消费的 CallEvent 数
            "window_samples": 0,     # 请求级样本数（进各模型窗口）
            "window_alerts": 0,      # 窗口告警发出
            "window_suppressed": 0,  # 冷却期内被抑制的窗口告警命中
            "fastpath_alerts": 0,    # 快速通道告警发出（每个连败 episode 恰 1 条）
            "send_dropped": 0,       # 通道未入队（可观测的丢；不再补发）
        }

    # ==================== 消费入口（hub CallEvent 分支调用；无 IO） ====================

    def on_call_event(self, event: "CallEvent") -> None:
        with self._lock:
            self.counters["seen"] += 1
            if not self._alerter.delivers():
                return
            model = event.model
            if event.status == STATUS_FAILED:
                # attempt 级：连败 +1；第 5 次即时告警（每个 episode 只报一次）
                streak = self._streaks.get(model, 0) + 1
                self._streaks[model] = streak
                if streak == FAST_PATH_STREAK:
                    self._fastpath_alert(event, streak)
            else:
                # success 重置连败；canceled 不进失败率（D4）且不延续连败
                #（严格 consecutive：取消算既不算失败也不算全败段的一部分）
                self._streaks[model] = 0
            if event.is_final and event.status != STATUS_CANCELED:
                # 请求级：最后一跳进出计分窗口（canceled 剔除分母，D4）
                now = self._clock()
                window = self._windows.setdefault(model, deque())
                window.append((now, event.status == STATUS_FAILED))
                while window and window[0][0] < now - WINDOW_SECONDS:
                    window.popleft()
                self.counters["window_samples"] += 1
                self._evaluate_window(event, now)

    # ==================== 触发点 ====================

    def _evaluate_window(self, event: "CallEvent", now: float) -> None:
        window = self._windows.get(event.model)
        if window is None or len(window) < WINDOW_MIN_REQUESTS:
            return
        fails = sum(1 for _, failed in window if failed)
        rate = fails / len(window)
        if rate <= WINDOW_FAIL_RATE:
            return
        last = self._last_window_alert.get(event.model)
        if last is not None and (now - last) < WINDOW_SILENCE_SECONDS:
            self.counters["window_suppressed"] += 1  # D11②：30min 冷却
            return
        message = AlertMessage(
            level=LEVEL_P1,
            rule=RULE_WINDOW,
            current=f"5min 失败 {fails}/{len(window)}（{rate:.0%}）",
            threshold=f"失败率 >{WINDOW_FAIL_RATE:.0%} 且请求数 ≥{WINDOW_MIN_REQUESTS}",
            ts=_event_ts(event.create_time),
            dimensions={"model": event.model},
            env=self._alerter.config.env,
            action="检查该模型上游错误率/配额；已触发熔断时以熔断通知为准",
        )
        if self._alerter.send(message):
            self.counters["window_alerts"] += 1
            self._last_window_alert[event.model] = now
        else:
            self.counters["send_dropped"] += 1  # 未入队不占冷却；下个触发点重试

    def _fastpath_alert(self, event: "CallEvent", streak: int) -> None:
        message = AlertMessage(
            level=LEVEL_P1,
            rule=RULE_FAST_PATH,
            current=f"连续 {streak} 次全败（attempt 级）",
            threshold=f"连续 {FAST_PATH_STREAK} 次",
            ts=_event_ts(event.create_time),
            dimensions={"model": event.model},
            env=self._alerter.config.env,
            action="模型疑似整体故障；检查上游可用性，任一成功即重置连败",
        )
        if self._alerter.send(message):
            self.counters["fastpath_alerts"] += 1
        else:
            # 连败 episode 唯一告警可能丢失——计数可见（send_dropped），本 episode 不补发
            self.counters["send_dropped"] += 1

    # ==================== /metrics 出口（D13） ====================

    def metrics(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "window_models": len(self._windows),   # 当前有窗口态的模型数
                "alerter": self._alerter.stats_snapshot(),
            }
