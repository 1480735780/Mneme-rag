# -*- coding: utf-8 -*-
"""
core.llm.observation.alert_budget - 预算水位告警 + P2 规则（P10.4b T4.4，D10/D11③/D6/D2）

预算告警（D11③ 字面语义）：
    - 水位：80% 预警一次 [P2] / 100% 报警一次 [P1] / 此后每再超 20% 追加一次 [P1]；
    - 冷却语义 = **同一水位不重复发**（水位账本 per 业务日；回落后再涨过
      未告警水位才补发）；日界（Asia/Shanghai 00:00）锚 business_day 标签，
      与看板 KPI / 月累计同源（T3.3 business_time 单一实现——告警与看板不互搏）；
    - 数据源 = DashboardService.load_cost()（同口径构造：当日聚合经 Condition API；
      只读已固化 cost_usd，从不重算——聚合口径与看板**按构造零分裂**）；
      定时评估（默认 60s，poll job 经 asyncio.to_thread 出事件循环，D2 同款）。

P2 规则（D10 默认分级收口）：
    - 估计占比：>30% [P2]（D6 一等公民；来源 = estimated_share.value_pct）；
    - 丢弃超阈值：dropped_full 达阈值 [P2]（D2「可观测的丢」的最终信号）；
    - 两者均每日一次（业务日语境），不做排班/升级重发（D10）。

预算/阈值配置（alerts yaml 扩展，校验即拒绝同 T4.1）：
    budget_daily_usd ≥ 0（0=不启用水位）；dropped_threshold ≥ 0（0=不启用）；
    estimation_ratio_pct ∈ [0, 100]（0=不启用）。

状态纪律：水位/daily-once 账本为进程内状态——重启后同水位可能重发一次
（宁可重复告警，不做磁盘持久化的多余状态；D10 不做未确认升级重发）。
调用纪律（D2/D12 同戒律）：evaluate() 全程无 IO（loader 的 IO 归 poll job
的 to_thread 线程），规则 bug 由 poll job 吞掉计数（poll_errors 进 metrics）。
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set

from core.llm.observation.alert import AlertMessage, LEVEL_P1, LEVEL_P2, WebhookAlerter

# ===================== 常量（D11③ 字面语义） =====================

WATERMARK_WARN = 80.0        # 80% 预警水位（P2 一次）
WATERMARK_BUDGET = 100.0     # 100% 报警水位（P1）
WATERMARK_STEP = 20.0        # 每 +20% 追加一次（P1）
POLL_INTERVAL_SECONDS = 60.0  # 默认评估周期

RULE_BUDGET_WARN = "budget.warn"
RULE_BUDGET_EXCEEDED = "budget.exceeded"
RULE_BUDGET_OVER = "budget.over"
RULE_ESTIMATION_RATIO = "estimation_ratio"
RULE_DROPS_THRESHOLD = "drops.threshold"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _watermark_level(ratio_pct: float) -> Optional[float]:
    """预算水位档位（%）：未达 80% → None；80..100 → 80（P2）；≥100 → 100+{20,40,…}"""
    if ratio_pct < WATERMARK_WARN:
        return None
    if ratio_pct < WATERMARK_BUDGET:
        return WATERMARK_WARN
    return WATERMARK_BUDGET + (ratio_pct - WATERMARK_BUDGET) // WATERMARK_STEP * WATERMARK_STEP


class BudgetAlertRule:
    """预算水位 + P2 规则（T4.4）：由 BudgetAlertPollJob 定时喂 KPI/statistics

    Args:
        alerter: 与其余告警共用同一 WebhookAlerter 出口。
        budget_daily_usd: 每日预算（USD；0=关闭水位告警）。
        dropped_threshold: 观测队列丢弃告警阈值（0=关闭）。
        estimation_ratio_pct: 估算占比告警阈值（%；0=关闭）。
    """

    def __init__(
        self,
        alerter: WebhookAlerter,
        *,
        budget_daily_usd: float = 0.0,
        dropped_threshold: int = 0,
        estimation_ratio_pct: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._alerter = alerter
        self._budget_daily_usd = budget_daily_usd
        self._dropped_threshold = dropped_threshold
        self._estimation_ratio_pct = estimation_ratio_pct
        self._clock = clock
        self._lock = threading.Lock()
        self._watermarks: Dict[str, float] = {}  # business_day → 已告警最高水位（%）
        self._daily_p2: Dict[str, Set[str]] = {}  # business_day → 已发过的 P2 规则名
        self._poll: Optional["BudgetAlertPollJob"] = None  # 挂上后 metrics 带租约状态
        # 语义计数（D2「可观测的丢」+ D13；随 metrics() 进 /metrics）
        self.counters: Dict[str, int] = {
            "evaluations": 0,        # evaluate() 次数（poll tick）
            "budget_alerts": 0,      # 水位告警发出
            "budget_suppressed": 0,  # 同水位去重（未重复发）
            "estimation_alerts": 0,  # 估算占比 P2 发出
            "drops_alerts": 0,       # 丢弃阈值 P2 发出
            "send_dropped": 0,       # 通道未入队（可观测的丢）计数
        }

    # poll job 回填（wiring 装配完成后调用，供 metrics 序列化租约状态）
    def attach_poll(self, poll: "BudgetAlertPollJob") -> None:
        self._poll = poll

    # ==================== 评估入口（poll job 调；无 IO） ====================

    def evaluate(self, kpi: Optional[Dict[str, Any]], stats: Optional[Dict[str, Any]]) -> None:
        """喂当日成本 KPI 与观测丢弃计数（任一为 None 跳过对应分支）"""
        with self._lock:
            self.counters["evaluations"] += 1
            if not self._alerter.delivers():
                return
            if kpi is not None:
                day = str(kpi.get("business_day") or "")
                if self._budget_daily_usd > 0:
                    self._evaluate_budget(kpi, day)
                if self._estimation_ratio_pct > 0:
                    self._evaluate_estimation(kpi, day)
            if stats is not None and self._dropped_threshold > 0:
                self._evaluate_drops(kpi, stats)

    def _evaluate_budget(self, kpi: Dict[str, Any], day: str) -> None:
        kpis = kpi.get("kpis") or {}
        cost = self._usd((kpis.get("cost_today") or {}).get("value_usd"))
        budget = self._budget_daily_usd
        ratio_pct = (cost * 100.0) / budget if budget > 0 else 0.0
        level = _watermark_level(ratio_pct)
        if level is None:
            return
        last = self._watermarks.get(day)
        if last is not None and level <= last:
            self.counters["budget_suppressed"] += 1  # D11③：同一水位不重复发
            return
        if self._send_budget_alert(day, ratio_pct, level, cost, budget):
            self._watermarks[day] = level  # 水位记账（回落后涨高才再发）

    def _send_budget_alert(
        self, day: str, ratio_pct: float, level: float, cost: float, budget: float
    ) -> bool:
        if level < WATERMARK_BUDGET:
            message = AlertMessage(
                level=LEVEL_P2,
                rule=RULE_BUDGET_WARN,
                current=f"当日成本 {cost:.2f} USD / 预算 {budget:.2f}（{ratio_pct:.0f}%）",
                threshold=f"预算 {level:.0f}%",
                ts=_now_iso(),
                dimensions={"day": day},
                env=self._alerter.config.env,
                action="预算水位预警：预估剩余消耗，必要时核价/降配",
            )
        elif level == WATERMARK_BUDGET:
            message = AlertMessage(
                level=LEVEL_P1,
                rule=RULE_BUDGET_EXCEEDED,
                current=f"当日成本 {cost:.2f} USD / 预算 {budget:.2f}（{ratio_pct:.0f}%）",
                threshold=f"预算 {level:.0f}%",
                ts=_now_iso(),
                dimensions={"day": day},
                env=self._alerter.config.env,
                action="当日预算已触顶：建议停投/缩量/复核价格配置",
            )
        else:
            message = AlertMessage(
                level=LEVEL_P1,
                rule=RULE_BUDGET_OVER,
                current=f"当日成本 {cost:.2f} USD / 预算 {budget:.2f}（{ratio_pct:.0f}%）",
                threshold=f"预算 {level:.0f}%",
                ts=_now_iso(),
                dimensions={"day": day},
                env=self._alerter.config.env,
                action=f"预算已超 {level - 100.0:.0f}%：建议立即停投并核对",
            )
        if self._alerter.send(message):
            self.counters["budget_alerts"] += 1
            return True
        self.counters["send_dropped"] += 1
        return False

    # ==================== P2 规则（每日一次，业务日语境） ====================

    def _evaluate_estimation(self, kpi: Dict[str, Any], day: str) -> None:
        kpis = kpi.get("kpis") or {}
        est_share = self._pct((kpis.get("estimated_share") or {}).get("value_pct"))
        if est_share <= self._estimation_ratio_pct:
            return
        if self._daily_once(day, RULE_ESTIMATION_RATIO):
            return
        message = AlertMessage(
            level=LEVEL_P2,
            rule=RULE_ESTIMATION_RATIO,
            current=f"估算占当日调用 {est_share:.1f}%",
            threshold=f">{self._estimation_ratio_pct:.0f}%",
            ts=_now_iso(),
            dimensions={"day": day},
            env=self._alerter.config.env,
            action="usage 缺失的 provider/调用点待接真——成本数字需打折看",
        )
        if self._alerter.send(message):
            self._mark_daily(day, RULE_ESTIMATION_RATIO)
            self.counters["estimation_alerts"] += 1
        else:
            self.counters["send_dropped"] += 1  # 每日一次窗口可留给下一评估

    def _evaluate_drops(self, kpi: Optional[Dict[str, Any]], stats: Dict[str, Any]) -> None:
        dropped = int(stats.get("dropped_full") or 0)
        if dropped < self._dropped_threshold:
            return
        day = str((kpi or {}).get("business_day") or "")
        if day and self._daily_once(day, RULE_DROPS_THRESHOLD):
            return  # 今日已发过（无业务日语境时按每次评估告警——进程生命周计数）
        message = AlertMessage(
            level=LEVEL_P2,
            rule=RULE_DROPS_THRESHOLD,
            current=f"观测队列累计丢弃 {dropped} 行",
            threshold=f"≥{self._dropped_threshold}",
            ts=_now_iso(),
            dimensions={"day": day},
            env=self._alerter.config.env,
            action="观测反噬风险：加 maxsize/增排 flush 慢（DB 慢查/锁竞争）",
        )
        if self._alerter.send(message):
            if day:
                self._mark_daily(day, RULE_DROPS_THRESHOLD)
            self.counters["drops_alerts"] += 1
        else:
            self.counters["send_dropped"] += 1

    def _daily_once(self, day: str, rule_name: str) -> bool:
        return rule_name in self._daily_p2.get(day, set())

    def _mark_daily(self, day: str, rule_name: str) -> None:
        self._daily_p2.setdefault(day, set()).add(rule_name)

    # ==================== /metrics 出口（D13） ====================

    def metrics(self) -> Dict[str, Any]:
        with self._lock:
            body: Dict[str, Any] = {
                "counters": dict(self.counters),
                "budget_daily_usd": self._budget_daily_usd,
                "dropped_threshold": self._dropped_threshold,
                "estimation_ratio_pct": self._estimation_ratio_pct,
                "day_watermarks": dict(self._watermarks),
                "alerter": self._alerter.stats_snapshot(),
            }
        if self._poll is not None:
            body["poll"] = self._poll.metrics()
        return body

    # helper：容错取数（KPI dict 缺键/类型异常一律回落 0——观测规则不因数据形态炸裂）
    @staticmethod
    def _usd(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _pct(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


# ===================== 定时评估 job（lifespan 启停，仿 knowledge_schedule_job） =====================


class BudgetAlertPollJob:
    """定时评估：默认 60s 一跳，load_cost 经 to_thread 出事件循环（D2 同款；DAO 线程安全先例）"""

    def __init__(
        self,
        rule: BudgetAlertRule,
        loader: Callable[[], Dict[str, Any]],
        *,
        interval_seconds: float = POLL_INTERVAL_SECONDS,
        stats_getter: Callable[[], Dict[str, Any]],
    ):
        self._rule = rule
        self._loader = loader
        self._stats_getter = stats_getter
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.metrics_state: Dict[str, int] = {"ticks": 0, "poll_errors": 0}
        rule.attach_poll(self)

    async def start(self) -> None:
        """启动定时评估协程（幂等；事件循环内调用——lifespan 启动点）"""
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止协程（不 drain——评估是探度信号而非数据落库，丢一跳无碍）"""
        self._stopping = True
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # 正常停机路径
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.wait_for(_sleep_tick(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass  # 到期：触发一跳评估
            if self._stopping:
                return
            await self.poll_once()

    async def poll_once(self) -> None:
        self.metrics_state["ticks"] += 1
        try:
            kpi = await asyncio.to_thread(self._loader)
            stats = self._stats_getter()
        except Exception:  # noqa: BLE001 —— loader 失败吞掉计数（与 flush_errors 同戒律）
            self.metrics_state["poll_errors"] += 1
            return
        try:
            self._rule.evaluate(kpi, stats)
        except Exception:  # noqa: BLE001 —— 规则 bug 吞掉计数（观测不反噬业务/loop）
            self.metrics_state["poll_errors"] += 1

    def metrics(self) -> Dict[str, Any]:
        return {"interval_seconds": self._interval, **self.metrics_state}


async def _sleep_tick() -> None:
    """可停的长睡眠点（外层 wait_for 按 interval 打断——定时与停机双响应）"""
    await asyncio.sleep(3600.0)
