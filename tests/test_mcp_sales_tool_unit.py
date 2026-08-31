# -*- coding: utf-8 -*-
"""
M2' sales_query 工具单测（对应 Java SalesMcpExecutor）

覆盖：
    - 数据集照抄 Java（B4：REGIONS/PRODUCTS/SALES_BY_REGION/CUSTOMER_POOL 逐条一致）
    - seed 确定性：seed = start.toEpochDay()（B1 思路：给定日期区间 → 数据稳定，非 Python hash）
    - 缓存：同 period 同日不重算（cacheKey = period + 日期）
    - 过滤：region/product/salesPerson
    - 四查询类型：summary（总销售额/订单/平均单价）/ ranking（第N名）/ detail（limit）/ trend（第N周）
"""
from datetime import date

from ragent_mcp.server.tools.sales import (
    CUSTOMER_POOL,
    PRODUCTS,
    REGIONS,
    SALES_BY_REGION,
    SALES_TOOL_NAME,
    _sales_seed,
    build_sales_tool_definition,
    generate_mock_data,
    get_or_generate_data,
    handle_sales_call,
)


class TestDataset:
    """B4：数据集逐条照抄 Java"""

    def test_regions(self):
        assert REGIONS == ["华东", "华南", "华北", "西南", "西北"]

    def test_products(self):
        assert PRODUCTS == ["企业版", "专业版", "基础版"]

    def test_sales_by_region(self):
        assert SALES_BY_REGION == {
            "华东": ["张三", "李四", "王五"],
            "华南": ["赵六", "钱七", "孙八"],
            "华北": ["周九", "吴十", "郑冬"],
            "西南": ["陈春", "林夏", "黄秋"],
            "西北": ["刘一", "杨二", "马三"],
        }

    def test_customer_pool(self):
        assert "腾讯科技" in CUSTOMER_POOL
        assert "海尔智家" in CUSTOMER_POOL
        assert len(CUSTOMER_POOL) == 20


class TestSeedDeterministic:
    """B1 思路：seed 用 epochDay（Java new Random(start.toEpochDay())），给定日期区间 → 稳定"""

    def test_seed_uses_epoch_day(self):
        d = date(2026, 8, 1)
        assert _sales_seed(d) == (d - date(1970, 1, 1)).days

    def test_data_stable_for_same_range(self):
        a = generate_mock_data(date(2026, 8, 1), date(2026, 8, 5))
        b = generate_mock_data(date(2026, 8, 1), date(2026, 8, 5))
        assert a == b  # 完整确定性

    def test_weekend_skipped(self):
        # 2026-08-01 是周六、08-02 是周日 → 两日皆跳过，无记录
        rows = generate_mock_data(date(2026, 8, 1), date(2026, 8, 2))
        assert rows == []
        # 8-03（周一）应有数据且字段合法
        rows = generate_mock_data(date(2026, 8, 3), date(2026, 8, 3))
        assert rows
        for r in rows:
            assert r["date"] == "2026-08-03"
            assert r["region"] in REGIONS
            assert r["product"] in PRODUCTS
            assert r["amount"] > 0


class TestCache:
    def test_same_period_same_day_cached(self):
        first = get_or_generate_data("本月")
        second = get_or_generate_data("本月")
        assert first is second  # 同对象（缓存命中）

    def test_period_in_cache_key(self):
        a = get_or_generate_data("本月")
        b = get_or_generate_data("上月")
        assert a is not b


class TestFilter:
    def test_filter_region(self):
        rows = generate_mock_data(date(2026, 8, 3), date(2026, 8, 7))
        from ragent_mcp.server.tools.sales import filter_data

        filtered = filter_data(rows, "华东", None, None)
        assert all(r["region"] == "华东" for r in filtered)

    def test_filter_product_and_sales(self):
        rows = generate_mock_data(date(2026, 8, 3), date(2026, 8, 7))
        from ragent_mcp.server.tools.sales import filter_data

        filtered = filter_data(rows, None, "企业版", "张三")
        assert all(r["product"] == "企业版" and r["sales_person"] == "张三" for r in filtered)


class TestHandleCall:
    def test_summary_fields(self):
        text, is_error = handle_sales_call({"period": "本月", "queryType": "summary"})
        assert is_error is False
        for field in ("销售数据汇总", "总销售额", "成交订单", "平均单价"):
            assert field in text

    def test_ranking_output(self):
        text, is_error = handle_sales_call({"period": "本月", "queryType": "ranking", "limit": 5})
        assert is_error is False
        assert "销售排名" in text
        assert "第1名" in text

    def test_detail_limit(self):
        text, is_error = handle_sales_call({"period": "本月", "queryType": "detail", "limit": 3})
        assert is_error is False
        assert "销售明细" in text
        assert "共 " in text and "条记录" in text

    def test_trend_output(self):
        text, is_error = handle_sales_call({"period": "本月", "queryType": "trend"})
        assert is_error is False
        assert "销售趋势" in text
        assert "周" in text

    def test_filter_in_summary(self):
        text, is_error = handle_sales_call({"period": "本月", "region": "华东", "queryType": "summary"})
        assert is_error is False
        assert "筛选条件" in text
        assert "地区: 华东" in text

    def test_limit_clamp(self):
        text, is_error = handle_sales_call({"period": "本月", "queryType": "detail", "limit": 0})
        assert is_error is False  # limit<=0 → 默认 10


class TestToolDefinition:
    def test_tool_name(self):
        assert SALES_TOOL_NAME == "sales_query"

    def test_input_schema(self):
        definition = build_sales_tool_definition()
        assert definition["name"] == "sales_query"
        schema = definition["input_schema"]
        assert schema["type"] == "object"
        assert "region" in schema["properties"]
        assert schema["properties"]["queryType"]["enum"] == ["summary", "ranking", "detail", "trend"]
        assert schema["properties"]["limit"]["default"] == 10
