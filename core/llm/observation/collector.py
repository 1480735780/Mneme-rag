# -*- coding: utf-8 -*-
"""
core.llm.observation.collector - 观测管线自健康计数器（P10.1，决策 D2/D13）

采集层与输出层分离：这里只维护内存 counters，/metrics（T1.2）与看板只是序列化出口；
将来若上 Prometheus，加一个 text format 序列化出口即可，采集代码零改动（D13）。

全部计数仅在事件循环内变更（投递/发射/flush 均在 loop 上），无需加锁。

「可观测的丢」（D2）：任何一条未落库路径都必须体现在某个计数器上，禁止静默消失——
观测系统自己失明等于没有观测。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class ObservationStats:
    """观测管线自健康计数（丢弃/失败可见，Q1/R2-Q1 冻结口径）"""

    enqueued: int = 0        # 成功入队条数
    dropped_full: int = 0    # 满载丢弃 + 停机后投递（均属「未能进入消费管道」）
    flushed: int = 0         # 已交付 handler 的条数（含 stop_and_drain）
    flush_errors: int = 0    # handler 抛异常的 flush 批次数（整批未落库，不重试）
    breaker_events: int = 0  # 熔断状态事件数（T1.7：进 recent 环形缓冲，告警消费在 T4.2）
    pricing_errors: int = 0  # 成本固化钩子异常行数（T1.8：吞掉计数，失败行照常落库兜底 unpriced）
    breaker_alert_errors: int = 0  # 熔断告警消费者异常数（T4.2：hub 兜底吞掉计数，不影响落账）
    call_alert_errors: int = 0     # 成功率告警规则异常数（T4.3：hub 兜底吞掉计数，不影响落账）
    budget_alert_errors: int = 0   # 预算/P2 规则序列化异常数（T4.4：hub 兜底吞掉计数）

    def snapshot(self) -> Dict[str, int]:
        """/metrics 序列化出口消费的只读快照（D13：采集与输出分离）"""
        return {
            "enqueued": self.enqueued,
            "dropped_full": self.dropped_full,
            "flushed": self.flushed,
            "flush_errors": self.flush_errors,
            "breaker_events": self.breaker_events,
            "pricing_errors": self.pricing_errors,
            "breaker_alert_errors": self.breaker_alert_errors,
            "call_alert_errors": self.call_alert_errors,
            "budget_alert_errors": self.budget_alert_errors,
        }
