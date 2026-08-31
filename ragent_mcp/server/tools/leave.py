# -*- coding: utf-8 -*-
"""
ragent_mcp.server.tools.leave - leave_query MCP 工具（对应 Java LeaveMcpExecutor，v1.1 审计 R-A 移植）

独立部署边界（D7）：本模块不 import rag/app/core。

对齐 Java 语义：
    - 假期类型：年假 / 调休 / 病假 / 事假；审批人池 李经理/王总监/赵主管
    - seed = Java String.hashCode(employeeName) * 31 + year（long 运算无溢出；Python 精度自然覆盖）
    - 年假：额度 = min(15, 10 + (工龄年-1)/3)（Java int 除法），全年额度 01-01 一次性发放，
      上年结转 1~5 天、当年 06-30 截止逾期清零，可用 = 额度 - (已休-结转已用) + 结转可用
    - 调休：加班 granting（90 天到期）FIFO 消耗，逾期清零
    - 病假/事假：不设额度，逐次登记
    - 每人每年数据按天缓存（cacheKey = employeeName_year_今日）
    - 两查询类型：balance（按类型三分支）/ detail（对齐 Java buildXxxResult 输出格式）
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ragent_mcp.server.tools.asset import java_string_hash

LEAVE_TOOL_NAME = "leave_query"

DEFAULT_EMPLOYEE = "张三"

LEAVE_TYPES = ["年假", "调休", "病假", "事假"]
APPROVERS = ["李经理", "王总监", "赵主管"]

_CARRY_DEADLINE_MONTH_DAY = (6, 30)  # 上年结转截止 06-30


@dataclass
class LeaveRecord:
    type: str
    start_date: str
    end_date: str
    days: float
    status: str
    approver: str


@dataclass
class CompensatoryGrant:
    overtime_date: str
    expire_date: str
    granted_days: float
    remaining_days: float


@dataclass
class LeaveProfile:
    employee_name: str
    year: int
    service_months: int
    hire_date: str
    quota: int
    carried_in: int
    leave_records: List[LeaveRecord] = field(default_factory=list)
    grants: List[CompensatoryGrant] = field(default_factory=list)

    def service_length(self) -> str:
        years, rest = divmod(self.service_months, 12)
        return f"{years} 年" if rest == 0 else f"{years} 年 {rest} 个月"

    def carry_deadline(self) -> str:
        return date(self.year, *_CARRY_DEADLINE_MONTH_DAY).isoformat()

    def records(self, leave_type: str) -> List[LeaveRecord]:
        return [r for r in self.leave_records if r.type == leave_type]

    def last_record(self, leave_type: str) -> Optional[LeaveRecord]:
        records = self.records(leave_type)
        return records[-1] if records else None

    def used_days(self, leave_type: str) -> float:
        return sum(r.days for r in self.records(leave_type))

    def carried_used(self) -> float:
        deadline = self.carry_deadline()
        used_before = sum(r.days for r in self.records("年假") if r.end_date <= deadline)
        return min(self.carried_in, used_before)

    def carry_deadline_passed(self, today: date) -> bool:
        return today > date(self.year, *_CARRY_DEADLINE_MONTH_DAY)

    def carried_expired(self, today: date) -> float:
        return self.carried_in - self.carried_used() if self.carry_deadline_passed(today) else 0

    def annual_available(self, today: date) -> float:
        carried_available = 0 if self.carry_deadline_passed(today) else self.carried_in - self.carried_used()
        return self.quota - (self.used_days("年假") - self.carried_used()) + carried_available

    def consume_grants(self) -> None:
        remaining = self.used_days("调休")
        for grant in self.grants:
            if remaining <= 0:
                break
            take = min(remaining, grant.remaining_days)
            grant.remaining_days -= take
            remaining -= take

    def valid_grants(self, today: date) -> List[CompensatoryGrant]:
        today_iso = today.isoformat()
        return [g for g in self.grants if g.remaining_days > 0 and g.expire_date >= today_iso]

    def compensatory_available(self, today: date) -> float:
        return sum(g.remaining_days for g in self.valid_grants(today))

    def compensatory_expired(self, today: date) -> float:
        today_iso = today.isoformat()
        return sum(
            g.remaining_days for g in self.grants
            if g.remaining_days > 0 and g.expire_date < today_iso
        )


# 模块级数据缓存（对齐 Java cachedProfile/cacheKey 字段）
_cached_profile: Optional[LeaveProfile] = None
_cache_key: Optional[str] = None


def _round_half(value: float) -> float:
    """对齐 Java Math.round(v*2)/2.0（0.5 天步进）"""
    return int(value * 2 + 0.5) / 2.0


def _days(value: float) -> str:
    """对齐 Java days(double)：整数不带小数，其余保留 1 位（0.5 步进值无舍入分歧）"""
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def _minus_months(today: date, months: int) -> date:
    import calendar

    month = today.month - months
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _generate_records(rng: random.Random, year: int, last_month: int, leave_type: str,
                      total_days: float) -> List[LeaveRecord]:
    records: List[LeaveRecord] = []
    remaining = total_days
    month = 1
    while remaining >= 0.5 and month <= last_month:
        take = min(remaining, 0.5 + 0.5 * rng.randrange(6))
        start = date(year, month, 1) + timedelta(days=rng.randrange(24))
        records.append(LeaveRecord(
            type=leave_type,
            days=take,
            start_date=start.isoformat(),
            end_date=(start + timedelta(days=max(0, math.ceil(take) - 1))).isoformat(),
            status="已批准",
            approver=APPROVERS[rng.randrange(len(APPROVERS))],
        ))
        remaining -= take
        month += 1 + rng.randrange(3)
    return records


def _generate_grants(rng: random.Random, year: int, last_month: int) -> List[CompensatoryGrant]:
    grants: List[CompensatoryGrant] = []
    count = 2 + rng.randrange(3)
    for _ in range(count):
        overtime = date(year, 1 + rng.randrange(last_month), 1) + timedelta(days=rng.randrange(27))
        grants.append(CompensatoryGrant(
            overtime_date=overtime.isoformat(),
            expire_date=(overtime + timedelta(days=90)).isoformat(),
            granted_days=0.5 + 0.5 * rng.randrange(4),
            remaining_days=0.0,  # 生成后统一赋值（对齐 Java 字段顺序）
        ))
    grants.sort(key=lambda g: g.overtime_date)
    for grant in grants:
        grant.remaining_days = grant.granted_days
    return grants


def _generate_profile(employee_name: str, year: int, today: date) -> LeaveProfile:
    rng = random.Random(java_string_hash(employee_name) * 31 + year)

    profile = LeaveProfile(
        employee_name=employee_name,
        year=year,
        service_months=24 + rng.randrange(72),
        hire_date="",
        quota=0,
        carried_in=1 + rng.randrange(5),
    )
    profile.hire_date = _minus_months(today, profile.service_months).isoformat()
    profile.quota = min(15, 10 + (profile.service_months // 12 - 1) // 3)

    last_month = today.month if year >= today.year else 12
    annual_budget = profile.quota + profile.carried_in
    profile.leave_records.extend(_generate_records(
        rng, year, last_month, "年假",
        _round_half(annual_budget * (0.35 + rng.random() * 0.35)),
    ))
    profile.leave_records.extend(_generate_records(
        rng, year, last_month, "病假", _round_half(rng.random() * 4),
    ))
    profile.leave_records.extend(_generate_records(
        rng, year, last_month, "事假", _round_half(rng.random() * 3),
    ))

    profile.grants.extend(_generate_grants(rng, year, last_month))
    compensatory_earned = sum(g.granted_days for g in profile.grants)
    profile.leave_records.extend(_generate_records(
        rng, year, last_month, "调休", _round_half(compensatory_earned * rng.random() * 0.6),
    ))
    profile.consume_grants()

    profile.leave_records.sort(key=lambda r: r.start_date)
    return profile


def get_or_generate_profile(employee_name: str, year: int, today: Optional[date] = None) -> LeaveProfile:
    """对齐 Java getOrGenerateProfile：缓存按 employeeName_year_今日；同 key 不重算"""
    global _cached_profile, _cache_key
    today = today or date.today()
    key = f"{employee_name}_{year}_{today}"
    if _cached_profile is not None and key == _cache_key:
        return _cached_profile
    _cached_profile = _generate_profile(employee_name, year, today)
    _cache_key = key
    return _cached_profile


# ==================== 输出格式化（对齐 Java buildXxxResult） ====================


def _build_balance(profile: LeaveProfile, leave_type: str, today: date) -> str:
    if leave_type == "调休":
        return _build_compensatory_balance(profile, today)
    if leave_type in ("病假", "事假"):
        return _build_plain_balance(profile, leave_type)
    return _build_annual_balance(profile, today)


def _build_annual_balance(profile: LeaveProfile, today: date) -> str:
    lines = [f"【{profile.employee_name} {profile.year} 年年假余额】", ""]
    lines.append(f"入职日期: {profile.hire_date}（工龄 {profile.service_length()}）")
    lines.append(f"全年额度: {_days(profile.quota)} 天，{profile.year}-01-01 一次性发放")
    lines.append(f"上年结转: {_days(profile.carried_in)} 天，截止 {profile.carry_deadline()}")
    lines.append(f"  结转部分已使用: {_days(profile.carried_used())} 天")
    lines.append(f"  结转部分逾期清零: {_days(profile.carried_expired(today))} 天")
    lines.append(f"本年已休: {_days(profile.used_days('年假'))} 天，共 {len(profile.records('年假'))} 次")
    lines.append(f"当前可用: {_days(profile.annual_available(today))} 天")

    last = profile.last_record("年假")
    if last is not None:
        lines.append(f"最近一次年假: {last.start_date} 至 {last.end_date}，{_days(last.days)} 天")
    return "\n".join(lines).strip()


def _build_compensatory_balance(profile: LeaveProfile, today: date) -> str:
    lines = [f"【{profile.employee_name} {profile.year} 年调休余额】", ""]
    lines.append(f"当前可用: {_days(profile.compensatory_available(today))} 天")
    lines.append(f"本年已休: {_days(profile.used_days('调休'))} 天")
    lines.append(f"逾期清零: {_days(profile.compensatory_expired(today))} 天")

    valid = profile.valid_grants(today)
    lines.append("")
    lines.append("【调休来源】")
    if not valid:
        lines.append("  当前无未到期的调休额度")
    else:
        for g in valid:
            lines.append(f"  {g.overtime_date} 加班 {_days(g.granted_days)} 天，{g.expire_date} 到期，剩余 {_days(g.remaining_days)} 天")
    return "\n".join(lines).strip()


def _build_plain_balance(profile: LeaveProfile, leave_type: str) -> str:
    lines = [f"【{profile.employee_name} {profile.year} 年{leave_type}使用情况】", ""]
    lines.append(f"本年已休: {_days(profile.used_days(leave_type))} 天，共 {len(profile.records(leave_type))} 次")

    last = profile.last_record(leave_type)
    if last is not None:
        lines.append(f"最近一次: {last.start_date} 至 {last.end_date}，{_days(last.days)} 天")
    lines.append(f"{leave_type}不设年度额度，按实际发生逐次登记")
    return "\n".join(lines).strip()


def _build_detail(profile: LeaveProfile, leave_type: str) -> str:
    records = profile.records(leave_type)
    lines = [f"【{profile.employee_name} {profile.year} 年{leave_type}请假明细】", ""]
    if not records:
        lines.append(f"本年度暂无{leave_type}记录")
        return "\n".join(lines).strip()
    lines.append(f"共 {len(records)} 条，合计 {_days(profile.used_days(leave_type))} 天：")
    lines.append("")
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. {r.start_date} 至 {r.end_date} | {_days(r.days)} 天 | {r.status} | 审批人: {r.approver}")
    return "\n".join(lines).strip()


# ==================== 工具声明 + 调用处理 ====================


def build_leave_tool_definition() -> Dict[str, Any]:
    return {
        "name": LEAVE_TOOL_NAME,
        "description": "查询员工的假期额度与余额，支持年假、调休、病假、事假，年假返回全年额度、上年结转天数与截止日、已休天数和当前可用余额，也可返回请假明细",
        "input_schema": {
            "type": "object",
            "properties": {
                "employeeName": {"type": "string", "description": "员工姓名或工号，不填则查询当前登录员工"},
                "leaveType": {"type": "string", "description": "假期类型：年假、调休、病假、事假，默认年假", "enum": LEAVE_TYPES, "default": "年假"},
                "year": {"type": "integer", "description": "查询年度，如 2026，不填则查询当前年度"},
                "queryType": {"type": "string", "description": "查询类型：balance(余额与额度)、detail(请假明细)", "enum": ["balance", "detail"], "default": "balance"},
            },
            "required": [],
        },
    }


def handle_leave_call(arguments: Dict[str, Any]) -> Tuple[str, bool]:
    """处理 leave_query 调用 → (text, is_error)；对齐 Java handleCall 参数校验"""
    try:
        employee_name = _str_or_none(arguments.get("employeeName")) or DEFAULT_EMPLOYEE
        leave_type = _str_or_none(arguments.get("leaveType"))
        if leave_type not in LEAVE_TYPES:
            leave_type = "年假"
        year = _int_or_none(arguments.get("year"))
        if year is None or year <= 0:
            year = date.today().year
        query_type = _str_or_none(arguments.get("queryType")) or "balance"

        profile = get_or_generate_profile(employee_name, year)

        text = _build_detail(profile, leave_type) if query_type == "detail" else _build_balance(profile, leave_type, date.today())
        return text, False
    except Exception as exc:  # noqa: BLE001 —— 对齐 Java catch(Exception) → errorResult
        return f"查询失败: {exc}", True


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None
