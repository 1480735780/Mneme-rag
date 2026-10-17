# -*- coding: utf-8 -*-
"""
common.util.business_time - 业务日界单一实现（P10.3 T3.3，决策 D11）

**Asia/Shanghai 00:00 日锚点，一段实现三处引用**：预算告警（P10.4b）/ 看板当日
KPI / 月累计成本——「当日」口径夹带任何分叉都会让告警与看板左右互搏一次就毁掉
信任（D11 原文）。

时间刻度纪律：行内 create_time 为 ISO 字符串，由存储侧 datetime.now() 物化——
**系统本地 naive 刻度**（InMemory 字典序比较、PG TIMESTAMP 列比较同一约定）。
本模块把东八区日锚点换回该刻度：

    1. 输入 naive now 视作系统本地时刻（与写行侧同刻度）→ astimezone(Asia/Shanghai)；
    2. 在东八区取 00:00 / 1 日 00:00 锚点；换回系统本地 naive 供 Condition 范围比较
       （服务器在东八区时恒等直取）。Asia/Shanghai 无夏令时，锚点换算线性可逆——
       非东八区服务器同样精确。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from zoneinfo import ZoneInfo

# 业务日界锚定时区（D11 原文：Asia/Shanghai 00:00）
BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class BusinessWindow:
    """业务窗口（同一时刻的两种刻度）：

        start / end      系统本地 naive 刻度——直接进 Condition/ISO 字符串比较；
        start_sh / end_sh  Asia/Shanghai aware 时刻——取 date 标签 / epoch 毫秒用。
    """

    start: datetime
    end: datetime
    start_sh: datetime
    end_sh: datetime

    def date_label(self) -> str:
        """窗口起始的东八区日期（YYYY-MM-DD，看板桶标签）"""
        return self.start_sh.date().isoformat()

    def start_epoch_ms(self) -> int:
        """窗口起始的 epoch 毫秒（前端趋势点的 ts）"""
        return int(self.start_sh.timestamp() * 1000)


def business_day_window(now: Optional[datetime] = None) -> BusinessWindow:
    """当日（Asia/Shanghai 00:00 起）的 [start, end) 业务日窗"""
    now = now or datetime.now()
    sh_start = now.astimezone(BUSINESS_ZONE).replace(  # naive 输入视作系统本地（与写行侧同刻度）
        hour=0, minute=0, second=0, microsecond=0
    )
    sh_end = sh_start + timedelta(days=1)
    return BusinessWindow(
        start=_to_local_naive(sh_start), end=_to_local_naive(sh_end),
        start_sh=sh_start, end_sh=sh_end,
    )


def business_month_window(now: Optional[datetime] = None) -> BusinessWindow:
    """当月（Asia/Shanghai 1 日 00:00 起）的 [start, end) 业务月窗（月累计 KPI 同源）"""
    now = now or datetime.now()
    sh_start = now.astimezone(BUSINESS_ZONE).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    # 次月一日：day=28 + 4 天必跨入下月（SH 无夏令时），再强制取 1 日——月长无关
    sh_end = (sh_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return BusinessWindow(
        start=_to_local_naive(sh_start), end=_to_local_naive(sh_end),
        start_sh=sh_start, end_sh=sh_end,
    )


def _to_local_naive(dt: datetime) -> datetime:
    """东八区 aware 时刻 → 系统本地 naive（行内 create_time 的存储刻度）"""
    return dt.astimezone().replace(tzinfo=None)
