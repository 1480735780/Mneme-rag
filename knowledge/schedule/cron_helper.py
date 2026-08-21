# -*- coding: utf-8 -*-
"""
knowledge.schedule.cron_helper - 调度 cron 工具（对应 Java CronScheduleHelper）

只暴露两个语义：给定 cron 的下次触发时间、给定 cron 的触发间隔是否小于阈值。不透传底层
表达式语义——croniter 与 Spring CronExpression 存在字段数/星期约定差异（Spring 6 字段含秒、
dow 0=周日；croniter 亦支持 6 字段秒级），对齐风险见 plan R3：本类把差异收敛在这两个语义上，
单测锁边界（秒级间隔 / 周日锚点 / 60s 下限）。

croniter 惰性导入：模块可在依赖未安装时被 import（语法/契约校验）；方法调用时才拉依赖，
缺失抛 RuntimeError 给出安装提示。对齐 Java 空/None 入参语义：cron 空或 from 为 None →
next_run_time 返回 None、is_interval_less_than 返回 True（Java hasText 分支）。

对应 ragent 源码：
    - knowledge/schedule/CronScheduleHelper（nextRunTime / isIntervalLessThan）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def _parse(cron: str):
    """解析 cron 表达式；非法返回 None，依赖缺失抛错（惰性 import）"""
    try:
        from croniter import CroniterBadCronError, croniter
    except ImportError as exc:  # coverage: 依赖未安装
        raise RuntimeError("缺少依赖 croniter>=2.0，请先 pip install croniter>=2.0") from exc
    try:
        # second_at_beginning=True：对齐 Spring 6 字段「首字段为秒」语义（dow 0=周日）；
        # 对 5 字段（按分钟）表达式同样适用（实测 * /1 * * * * 间隔仍为 60s）
        return croniter(cron, second_at_beginning=True)
    except CroniterBadCronError:
        return None


def _has_text(cron: Optional[str]) -> bool:
    """对齐 Java StringUtils.hasText"""
    return bool(cron and cron.strip())


class CronScheduleHelper:
    """cron 解析静态工具（对应 Java 同名 final 工具类，全静态方法）"""

    @staticmethod
    def validate(cron: Optional[str]) -> bool:
        """cron 表达式是否可解析（语法合法）；空/None 视为不合法"""
        if not _has_text(cron):
            return False
        return _parse(cron.strip()) is not None

    @staticmethod
    def next_run_time(cron: Optional[str], from_time: Optional[datetime]) -> Optional[datetime]:
        """给定 cron 在 from_time 之后的下一次触发时间；空 cron / None from / 非法 → None"""
        if not _has_text(cron) or from_time is None:
            return None
        iterator = _parse(cron.strip())
        if iterator is None:
            return None
        return iterator.get_next(datetime, start_time=from_time)

    @staticmethod
    def is_interval_less_than(cron: Optional[str], from_time: Optional[datetime], min_seconds: int) -> bool:
        """触发间隔是否小于 min_seconds（对齐 Java isIntervalLessThan）

        取 from 之后的前两个触发点求间隔；空 cron / None from / 非法 / 无后续触发点 → True
        （保守：无法证明≥阈值时按「过密」处理，交由上层拒绝或回落）。
        """
        if not _has_text(cron) or from_time is None:
            return True
        iterator = _parse(cron.strip())
        if iterator is None:
            return True
        first = iterator.get_next(datetime, start_time=from_time)
        second = iterator.get_next(datetime, start_time=first)
        if first is None or second is None:
            return True
        return (second - first).total_seconds() < min_seconds