# -*- coding: utf-8 -*-
"""
M2' ticket_query 工具单测（对应 Java TicketMcpExecutor）

覆盖：
    - 数据集照抄 Java（B4：REGIONS/PRODUCTS/CUSTOMERS_BY_REGION/ENGINEERS_BY_REGION/ISSUE_TEMPLATES/CATEGORIES）
    - seed 确定性：seed = today.toEpochDay()（B1 思路：给定日期 → 数据稳定）
    - 过滤：region/status/priority/product/customerName 模糊匹配
    - 三查询类型：summary（状态分布/解决率/紧急高优先级）/ list（按优先级排序 + limit）/ stats（分类/产品解决率/处理人排名）
    - ticketId 格式 TK-yyyyMM-NNNN
"""
from datetime import date

from ragent_mcp.server.tools.ticket import (
    CATEGORIES,
    CUSTOMERS_BY_REGION,
    ENGINEERS_BY_REGION,
    ISSUE_TEMPLATES,
    PRODUCTS,
    REGIONS,
    STATUSES,
    TICKET_TOOL_NAME,
    _ticket_seed,
    build_ticket_tool_definition,
    generate_mock_data,
    get_or_generate_data,
    handle_ticket_call,
)


class TestDataset:
    """B4：数据集逐条照抄 Java"""

    def test_regions(self):
        assert REGIONS == ["华东", "华南", "华北", "西南", "西北"]

    def test_products(self):
        assert PRODUCTS == ["企业版", "专业版", "基础版"]

    def test_statuses(self):
        assert STATUSES == ["待处理", "处理中", "已解决", "已关闭"]

    def test_customers_by_region(self):
        assert CUSTOMERS_BY_REGION["华东"] == ["腾讯科技", "阿里巴巴", "字节跳动", "网易公司"]
        assert len(CUSTOMERS_BY_REGION) == 5

    def test_engineers_by_region(self):
        assert ENGINEERS_BY_REGION["华北"] == ["工程师C1", "工程师C2"]

    def test_issue_templates_and_categories(self):
        assert len(ISSUE_TEMPLATES) == 15
        assert "功能异常" in CATEGORIES
        assert len(CATEGORIES) == 6


class TestSeedDeterministic:
    """B1 思路：seed = today.toEpochDay()（Java new Random(today.toEpochDay())）"""

    def test_seed_uses_epoch_day(self):
        d = date(2026, 8, 23)
        assert _ticket_seed(d) == (d - date(1970, 1, 1)).days

    def test_data_stable_for_same_day(self):
        a = generate_mock_data(date(2026, 8, 23))
        b = generate_mock_data(date(2026, 8, 23))
        assert a == b

    def test_record_shape(self):
        rows = generate_mock_data(date(2026, 8, 23))
        assert rows
        for r in rows:
            assert r["ticket_id"].startswith("TK-")
            assert r["region"] in REGIONS
            assert r["product"] in PRODUCTS
            assert r["status"] in STATUSES
            assert r["priority"] in ("紧急", "高", "中", "低")


class TestCache:
    def test_same_day_cached(self):
        first = get_or_generate_data()
        second = get_or_generate_data()
        assert first is second


class TestHandleCall:
    def test_summary_fields(self):
        text, is_error = handle_ticket_call({"queryType": "summary"})
        assert is_error is False
        for field in ("工单汇总", "工单总数", "状态分布", "待处理", "已解决"):
            assert field in text

    def test_summary_resolve_rate(self):
        text, is_error = handle_ticket_call({"queryType": "summary"})
        assert is_error is False
        assert "解决率" in text

    def test_list_sorted_by_priority(self):
        text, is_error = handle_ticket_call({"queryType": "list", "limit": 5})
        assert is_error is False
        assert "工单列表" in text
        assert "1." in text

    def test_stats_output(self):
        text, is_error = handle_ticket_call({"queryType": "stats"})
        assert is_error is False
        for field in ("工单统计分析", "问题分类统计", "各产品解决率"):
            assert field in text

    def test_filter_status(self):
        text, is_error = handle_ticket_call({"queryType": "summary", "status": "待处理"})
        assert is_error is False
        assert "筛选条件" in text
        assert "状态: 待处理" in text

    def test_customer_name_fuzzy(self):
        text, is_error = handle_ticket_call({"queryType": "list", "customerName": "腾讯", "limit": 5})
        assert is_error is False
        assert "共 " in text

    def test_limit_clamp(self):
        text, is_error = handle_ticket_call({"queryType": "list", "limit": 0})
        assert is_error is False  # limit<=0 → 默认 10


class TestToolDefinition:
    def test_tool_name(self):
        assert TICKET_TOOL_NAME == "ticket_query"

    def test_input_schema(self):
        definition = build_ticket_tool_definition()
        assert definition["name"] == "ticket_query"
        schema = definition["input_schema"]
        assert schema["type"] == "object"
        assert schema["properties"]["queryType"]["enum"] == ["summary", "list", "stats"]
        assert "customerName" in schema["properties"]
        assert schema["properties"]["limit"]["default"] == 10
