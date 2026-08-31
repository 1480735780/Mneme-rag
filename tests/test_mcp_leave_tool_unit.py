# -*- coding: utf-8 -*-
"""
R-A leave_query 工具单测（对应 Java LeaveMcpExecutor，v1.1 审计 R-A 移植）

覆盖：
    - 假期类型/审批人池照抄 Java
    - seed = java_hash(name)*31 + year → 同名同年数据稳定、跨年不同
    - 额度公式：quota = min(15, 10 + (工龄年-1)//3)；hire_date = 今日 - 工龄月
    - 年假余额：结转截止 06-30 / 逾期清零 / 当前可用公式；历史年度（截止已过）语义
    - 调休：加班 granting 90 天到期 + FIFO 消耗；病假/事假不设额度
    - detail：明细列表 + 空类型回落
    - 默认值与错误路径（对齐 Java handleCall）
"""
from datetime import date

from ragent_mcp.server.tools.asset import java_string_hash
from ragent_mcp.server.tools.leave import (
    APPROVERS,
    DEFAULT_EMPLOYEE,
    LEAVE_TOOL_NAME,
    LEAVE_TYPES,
    _generate_profile,
    build_leave_tool_definition,
    get_or_generate_profile,
    handle_leave_call,
)


class TestDataset:
    def test_leave_types_and_approvers(self):
        assert LEAVE_TYPES == ["年假", "调休", "病假", "事假"]
        assert APPROVERS == ["李经理", "王总监", "赵主管"]


class TestProfileGeneration:
    def test_seed_deterministic_same_name_year(self):
        today = date(2026, 8, 30)
        a = _generate_profile("张三", 2026, today)
        b = _generate_profile("张三", 2026, today)
        assert [(r.type, r.start_date, r.days) for r in a.leave_records] == \
               [(r.type, r.start_date, r.days) for r in b.leave_records]
        assert [(g.overtime_date, g.granted_days) for g in a.grants] == \
               [(g.overtime_date, g.granted_days) for g in b.grants]

    def test_seed_differs_across_years(self):
        today = date(2026, 8, 30)
        a = _generate_profile("张三", 2025, today)
        b = _generate_profile("张三", 2026, today)
        # seed = hash*31 + year → 序列不同（至少记录数或起点不同）
        assert [(r.start_date) for r in a.leave_records] != [(r.start_date) for r in b.leave_records]

    def test_quota_formula(self):
        today = date(2026, 8, 30)
        profile = _generate_profile("张三", 2026, today)
        expected = min(15, 10 + (profile.service_months // 12 - 1) // 3)
        assert profile.quota == expected
        assert 10 <= profile.quota <= 15  # 工龄 2~7 年 → 额度 10~12

    def test_hire_date_consistent(self):
        today = date(2026, 8, 30)
        profile = _generate_profile("张三", 2026, today)
        assert 24 <= profile.service_months < 96
        assert len(profile.hire_date) == 10

    def test_leave_records_have_half_day_steps(self):
        today = date(2026, 8, 30)
        profile = _generate_profile("张三", 2026, today)
        for r in profile.leave_records:
            assert r.days * 2 == int(r.days * 2)  # 0.5 天步进
            assert r.status == "已批准"
            assert r.approver in APPROVERS
            assert r.end_date >= r.start_date

    def test_compensatory_fifo_consumed(self):
        # FIFO 消耗不变式：按 overtimeDate 顺序扣减 → 部分消耗（0 < 剩余 < 原额）的 granting 至多一个
        today = date(2026, 8, 30)
        profile = _generate_profile("张三", 2026, today)
        partial = [
            g for g in profile.grants
            if 0 < g.remaining_days < g.granted_days
        ]
        assert len(partial) <= 1
        for g in profile.grants:
            assert 0 <= g.remaining_days <= g.granted_days


class TestBalanceOutput:
    def test_annual_balance_current_year(self):
        profile = get_or_generate_profile("张三", date.today().year)
        text, is_error = handle_leave_call({"employeeName": "张三", "leaveType": "年假"})
        assert not is_error
        assert f"【张三 {date.today().year} 年年假余额】" in text
        assert "入职日期:" in text and "工龄" in text
        assert f"全年额度: {_days_fmt(profile.quota)} 天，{date.today().year}-01-01 一次性发放" in text
        assert "上年结转:" in text and "截止" in text
        assert "结转部分已使用:" in text and "结转部分逾期清零:" in text
        assert "当前可用:" in text

    def test_annual_balance_past_year_expired(self):
        past = date.today().year - 1
        text, is_error = handle_leave_call({"employeeName": "张三", "leaveType": "年假", "year": past})
        assert not is_error
        # 历史年度结转截止必已过 → 逾期清零行存在且为全部未用结转
        assert "结转部分逾期清零:" in text
        assert "当前可用:" in text

    def test_compensatory_balance(self):
        text, is_error = handle_leave_call({"employeeName": "张三", "leaveType": "调休"})
        assert not is_error
        assert "调休余额】" in text
        assert "当前可用:" in text and "逾期清零:" in text
        assert "【调休来源】" in text

    def test_plain_balance_sick(self):
        text, is_error = handle_leave_call({"employeeName": "张三", "leaveType": "病假"})
        assert not is_error
        assert "病假使用情况】" in text
        assert "本年已休:" in text
        assert "病假不设年度额度，按实际发生逐次登记" in text

    def test_detail_output(self):
        text, is_error = handle_leave_call({"employeeName": "张三", "leaveType": "年假", "queryType": "detail"})
        assert not is_error
        assert "年假请假明细】" in text
        assert ("共 " in text and "合计" in text) or "本年度暂无" in text
        if "共 " in text:
            assert "审批人:" in text


class TestDefaultsAndErrors:
    def test_defaults(self):
        text, is_error = handle_leave_call({})
        assert not is_error
        assert f"【{DEFAULT_EMPLOYEE} {date.today().year} 年年假余额】" in text

    def test_invalid_leave_type_falls_back(self):
        text, is_error = handle_leave_call({"employeeName": "张三", "leaveType": "相亲假"})
        assert not is_error
        assert "年假余额】" in text

    def test_invalid_year_falls_back_to_current(self):
        text, is_error = handle_leave_call({"employeeName": "张三", "year": -5})
        assert not is_error
        assert f"{date.today().year} 年年假余额】" in text

    def test_exception_maps_to_error(self, monkeypatch):
        import ragent_mcp.server.tools.leave as leave_mod

        def boom(*args, **kwargs):
            raise RuntimeError("模拟故障")

        monkeypatch.setattr(leave_mod, "get_or_generate_profile", boom)
        text, is_error = handle_leave_call({"employeeName": "张三"})
        assert is_error
        assert "查询失败: 模拟故障" in text


def test_tool_definition_contract():
    definition = build_leave_tool_definition()
    assert definition["name"] == LEAVE_TOOL_NAME
    schema = definition["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == []
    assert schema["properties"]["leaveType"]["enum"] == LEAVE_TYPES
    assert schema["properties"]["leaveType"]["default"] == "年假"
    assert schema["properties"]["queryType"]["enum"] == ["balance", "detail"]


def _days_fmt(value) -> str:
    return str(int(value)) if value == int(value) else f"{value:.1f}"
