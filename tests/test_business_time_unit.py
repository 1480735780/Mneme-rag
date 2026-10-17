# -*- coding: utf-8 -*-
"""
P10.3 T3.3 单测：业务日界单一实现（common.util.business_time，D11）

约束：Asia/Shanghai 00:00 日锚点；输出两种刻度——start/end 为系统本地 naive
（与行内 ISO 字符串直接比较）、start_sh/end_sh 为东八区 aware（date 标签/epoch 用）。

时区纪律：**全部用 aware 输入驱动**（如 UTC 16:00 = 东八区次日 00:00）——
astimezone 对 aware 输入不依赖测试机本地时区，任何机器上结果确定。
"""
from datetime import datetime, timedelta, timezone

from common.util.business_time import (
    BUSINESS_ZONE,
    business_day_window,
    business_month_window,
)


def _sh_midnight_utc(y: int, m: int, d: int) -> datetime:
    """东八区某日 00:00 对应的 UTC aware 时刻（测试输入的确定构造）"""
    return datetime(y, m, d, 0, 0, tzinfo=BUSINESS_ZONE).astimezone(timezone.utc)


# ==================== 日窗语义 ====================


def test_day_window_anchor_is_shanghai_midnight():
    now = _sh_midnight_utc(2026, 10, 18) + timedelta(minutes=5)  # SH 10-18 00:05
    day = business_day_window(now)
    assert day.start_sh.date().isoformat() == "2026-10-18"
    assert day.date_label() == "2026-10-18"
    assert day.end_sh - day.start_sh == timedelta(days=1)  # 恒 24h（SH 无夏令时）
    # 本地 naive 与 aware 同一时刻的双刻度一致性（与测试机时区无关）
    assert day.start == day.start_sh.astimezone().replace(tzinfo=None)
    assert day.end == day.end_sh.astimezone().replace(tzinfo=None)


def test_day_window_late_evening_clamps_to_same_day():
    # UTC 15:59 = SH 当日 23:59 —— 与 00:01 同属一个业务日
    late = _sh_midnight_utc(2026, 10, 18) + timedelta(hours=23, minutes=59)
    early_next = _sh_midnight_utc(2026, 10, 18) + timedelta(hours=24, minutes=1)
    late_day = business_day_window(late)
    next_day = business_day_window(early_next)
    assert late_day.date_label() == "2026-10-18"
    assert next_day.date_label() == "2026-10-19"
    assert late_day.end == next_day.start  # 相邻业务日窗口无缝衔接


def test_day_window_epoch_ms_is_absolute():
    day = business_day_window(_sh_midnight_utc(2026, 10, 18))
    assert day.start_epoch_ms() == int(day.start_sh.timestamp() * 1000)


# ==================== 月窗语义（月长无关） ====================


def test_month_window_31_day_month():
    month = business_month_window(_sh_midnight_utc(2026, 10, 18) + timedelta(hours=12))
    assert month.start_sh.date().isoformat() == "2026-10-01"
    assert month.end_sh.date().isoformat() == "2026-11-01"


def test_month_window_crosses_year_boundary():
    # SH 2026-12-31 23:59 仍属 12 月窗
    month = business_month_window(_sh_midnight_utc(2026, 12, 31) + timedelta(hours=23, minutes=59))
    assert month.start_sh.date().isoformat() == "2026-12-01"
    assert month.end_sh.date().isoformat() == "2027-01-01"


def test_month_window_28_day_month():
    month = business_month_window(_sh_midnight_utc(2026, 2, 14) + timedelta(hours=12))
    assert month.start_sh.date().isoformat() == "2026-02-01"
    assert month.end_sh.date().isoformat() == "2026-03-01"


def test_month_window_contains_day_window():
    now = _sh_midnight_utc(2026, 10, 18) + timedelta(hours=9)
    month = business_month_window(now)
    day = business_day_window(now)
    assert month.start <= day.start < day.end <= month.end  # 当日 ⊆ 当月（KPI 同源前提）
