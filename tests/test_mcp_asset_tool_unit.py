# -*- coding: utf-8 -*-
"""
R-A asset_query 工具单测（对应 Java AssetMcpExecutor，v1.1 审计 R-A 移植）

覆盖：
    - 数据集照抄 Java（CATEGORIES/STATUSES/SERVICE_LIMIT_MONTHS/CATEGORY_CODES/MODELS/LOCATIONS）
    - seed = Java String.hashCode（自实现 32 位有符号；"abc"→96354 / "张三"→773897 为 Java 已知值）
    - 数据形态：资产编号前缀/服役月数一致性/首台笔记本（49+ 月）必达 48 月换新年限
    - 缓存：同 employeeName 同日不重算（对象同一性）
    - 过滤：assetType / status
    - 三查询类型：summary / list（limit）/ renewal 输出契约
    - 默认值与错误路径（对齐 Java handleCall）
"""
from datetime import date

import pytest

from ragent_mcp.server.tools.asset import (
    CATEGORIES,
    CATEGORY_CODES,
    DEFAULT_EMPLOYEE,
    LOCATIONS,
    MODELS,
    SERVICE_LIMIT_MONTHS,
    STATUSES,
    ASSET_TOOL_NAME,
    _generate_mock_data,
    build_asset_tool_definition,
    get_or_generate_data,
    handle_asset_call,
    java_string_hash,
)


class TestDataset:
    """数据集逐条照抄 Java"""

    def test_categories_and_statuses(self):
        assert CATEGORIES == ["笔记本电脑", "台式机", "显示器", "扩展坞", "移动硬盘", "测试手机"]
        assert STATUSES == ["在用", "维修中", "借用中", "待归还"]

    def test_service_limits(self):
        assert SERVICE_LIMIT_MONTHS == {
            "笔记本电脑": 48, "台式机": 60, "显示器": 72,
            "扩展坞": 36, "移动硬盘": 36, "测试手机": 36,
        }

    def test_category_codes(self):
        assert CATEGORY_CODES == {
            "笔记本电脑": "NB", "台式机": "PC", "显示器": "MT",
            "扩展坞": "DK", "移动硬盘": "HD", "测试手机": "MP",
        }

    def test_models_and_locations(self):
        assert MODELS["笔记本电脑"] == ["ThinkPad X1 Carbon 32G/1T", "MacBook Pro 14 32G/1T", "Dell XPS 15 32G/1T"]
        assert len(LOCATIONS) == 4


class TestSeed:
    """seed = Java String.hashCode（Python hash() 进程内随机化，须自实现）"""

    def test_java_hash_known_values(self):
        # Java 已知值："abc".hashCode() == 96354
        assert java_string_hash("abc") == 96354
        # "张三"：31*0x5F20 + 0x4E09 = 774889
        assert java_string_hash("张三") == 774889

    def test_java_hash_negative_wraps(self):
        # 32 位有符号溢出：足够长的串触发负值
        assert isinstance(java_string_hash(" sehr langer string mit ümlaut"), int)

    def test_data_stable_for_same_name(self):
        today = date(2026, 8, 30)
        a = _generate_mock_data("张三", today)
        b = _generate_mock_data("张三", today)
        assert [(r.asset_no, r.receive_date) for r in a] == [(r.asset_no, r.receive_date) for r in b]


class TestDataShape:
    def test_first_laptop_reaches_limit(self):
        # 首台笔记本服役 49~59 月 > 48 月上限 → 必达换新年限
        today = date.today()
        data = _generate_mock_data("张三", today)
        laptop = next(r for r in data if r.category == "笔记本电脑")
        assert laptop.served_months(today) >= laptop.service_limit_months
        assert laptop.reached_service_limit(today)

    def test_record_fields(self):
        today = date(2026, 8, 30)
        data = _generate_mock_data("李四", today)
        for r in data:
            assert r.asset_no.startswith(f"IT-{CATEGORY_CODES[r.category]}-")
            assert r.model in MODELS[r.category]
            assert r.status in STATUSES
            assert r.location in LOCATIONS
            assert 0 <= r.served_months(today)

    def test_cache_same_name_same_day_identity(self):
        get_or_generate_data._cached_data = None  # type: ignore[attr-defined]
        a = get_or_generate_data("王五")
        b = get_or_generate_data("王五")
        assert a is b


class TestFilters:
    def test_filter_by_category(self):
        today = date(2026, 8, 30)
        data = _generate_mock_data("张三", today)
        from ragent_mcp.server.tools.asset import _filter_data
        filtered = _filter_data(data, "显示器", None)
        assert all(r.category == "显示器" for r in filtered)

    def test_filter_by_status(self):
        today = date(2026, 8, 30)
        data = _generate_mock_data("张三", today)
        from ragent_mcp.server.tools.asset import _filter_data
        filtered = _filter_data(data, None, "在用")
        assert all(r.status == "在用" for r in filtered)


class TestQueryTypes:
    def test_summary_contract(self):
        text, is_error = handle_asset_call({"employeeName": "张三", "queryType": "summary"})
        assert not is_error
        assert "名下 IT 资产汇总】" in text
        assert "资产总数:" in text
        assert "状态分布:" in text
        assert "【按类别】" in text and "【资产清单】" in text
        assert "【换新提示】" in text
        # 首台笔记本（49+ 月 > 48 月上限）必在换新清单中；件数随随机命中 1~2 件
        renewable_line = next(line for line in text.splitlines() if line.startswith("  已达服役年限:"))
        assert "笔记本电脑" in renewable_line

    def test_list_respects_limit(self):
        text, is_error = handle_asset_call({"employeeName": "张三", "queryType": "list", "limit": 2})
        assert not is_error
        assert "显示 2 件：" in text
        assert "1. IT-" in text and "2. IT-" in text
        assert "3. IT-" not in text

    def test_renewal_contract(self):
        text, is_error = handle_asset_call({"employeeName": "张三", "queryType": "renewal"})
        assert not is_error
        assert "资产换新资格检查】" in text
        assert "已达年限，可提交换新工单" in text
        assert "距可申请还有" in text
        assert "本结果仅为年限判定" in text

    def test_empty_filter_result(self):
        # 张三数据只有 在用/借用中 → 维修中 过滤为空
        text, is_error = handle_asset_call({"employeeName": "张三", "status": "维修中"})
        assert not is_error
        assert "名下暂无符合条件的资产记录" in text


class TestDefaultsAndErrors:
    def test_defaults(self):
        text, is_error = handle_asset_call({})
        assert not is_error
        assert f"【{DEFAULT_EMPLOYEE} 名下 IT 资产汇总】" in text  # 缺省员工张三 / 缺省 summary

    def test_unknown_query_type_falls_back_to_summary(self):
        text, _ = handle_asset_call({"employeeName": "张三", "queryType": "unknown"})
        assert "汇总】" in text

    def test_invalid_limit_falls_back(self):
        text, is_error = handle_asset_call({"employeeName": "张三", "queryType": "list", "limit": -1})
        assert not is_error
        assert "显示" in text

    def test_exception_maps_to_error(self, monkeypatch):
        import ragent_mcp.server.tools.asset as asset_mod

        def boom(*args, **kwargs):
            raise RuntimeError("模拟故障")

        monkeypatch.setattr(asset_mod, "get_or_generate_data", boom)
        text, is_error = handle_asset_call({"employeeName": "张三"})
        assert is_error
        assert "查询失败: 模拟故障" in text


def test_tool_definition_contract():
    definition = build_asset_tool_definition()
    assert definition["name"] == ASSET_TOOL_NAME
    schema = definition["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == []
    assert schema["properties"]["assetType"]["enum"] == CATEGORIES
    assert schema["properties"]["status"]["enum"] == STATUSES
    assert schema["properties"]["queryType"]["default"] == "summary"
    assert schema["properties"]["limit"]["default"] == 20
