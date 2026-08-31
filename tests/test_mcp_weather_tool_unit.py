# -*- coding: utf-8 -*-
"""
M1' weather_query 工具单测（对应 Java WeatherMcpExecutor）

覆盖（B1 seed 确定性为核心）：
    - _java_hash_code：移植 Java String.hashCode() 31 多项式，断言已知值（北京=679541），
      禁止 Python 内置 hash（PYTHONHASHSEED 盐跨进程漂移）
    - _epoch_day：Java LocalDate.toEpochDay 等价（2000-01-01 → 10957）
    - _weather_seed：seed = epochDay*31 + java_hash(city)，确定性
    - generate_weather_for_date：同日同城两次完全一致；季节温度范围合理；未知城市报错
    - 工具定义：name=weather_query、input_schema 含 city 必填、queryType enum、days 默认 3
    - handle_weather_call：current 字段齐 / forecast 天数钳制（<=0→3、>7→7）/ 未知城市 isError
"""
from datetime import date

from ragent_mcp.server.tools.weather import (
    WEATHER_TOOL_NAME,
    _epoch_day,
    _java_hash_code,
    _weather_seed,
    build_weather_tool_definition,
    generate_weather_for_date,
    handle_weather_call,
)


class TestJavaHashCode:
    """B1：必须移植 Java String.hashCode()（31 多项式 + 32 位有符号回绕），严禁 hash(city)"""

    def test_known_value_beijing(self):
        # 手工计算：北=U+5317(21271) 京=U+4EAC(20140)
        # h=31*21271+20140=679541（<2^31，无回绕）
        assert _java_hash_code("北京") == 679541

    def test_known_value_ascii(self):
        # "abc"：h = 31*0+97=97 → 31*97+98=3105 → 31*3105+99=96354
        assert _java_hash_code("abc") == 96354

    def test_deterministic_across_calls(self):
        assert _java_hash_code("上海") == _java_hash_code("上海")
        # 上海=U+4E0A(19978) 海=U+6D77(28023)：31*19978+28023=619318+28023=647341
        assert _java_hash_code("上海") == 647341

    def test_overflow_wraps_32bit_signed(self):
        # 长字符串触发 32 位回绕：h 须落在 [-2^31, 2^31)
        h = _java_hash_code("哈尔滨哈尔滨哈尔滨哈尔滨")
        assert -(2**31) <= h < 2**31

    def test_differs_from_python_hash(self):
        # 关键：不能等于 Python 内置 hash（带盐）。北京已知值 679541 已排除内置 hash 可能
        assert _java_hash_code("北京") != hash("北京")


class TestEpochDay:
    def test_epoch_zero(self):
        assert _epoch_day(date(1970, 1, 1)) == 0

    def test_known_value_2000(self):
        assert _epoch_day(date(2000, 1, 1)) == 10957

    def test_seed_uses_epoch_day(self):
        # seed = epochDay*31 + java_hash(city)；2026-08-23 的 epochDay 可独立推算
        d = date(2026, 8, 23)
        expected_epoch = (d - date(1970, 1, 1)).days
        assert _epoch_day(d) == expected_epoch
        assert _weather_seed("北京", d) == expected_epoch * 31 + 679541


class TestSeedDeterministic:
    def test_seed_stable_for_city_date(self):
        d = date(2026, 8, 23)
        assert _weather_seed("北京", d) == _weather_seed("北京", d)
        # 不同日期 → 不同 seed（同一城市跨天漂移是预期的，只要确定性）
        assert _weather_seed("北京", d) != _weather_seed("北京", date(2026, 8, 24))


class TestGenerateWeather:
    def test_same_day_same_city_stable(self):
        d = date(2026, 8, 23)
        a = generate_weather_for_date("北京", d)
        b = generate_weather_for_date("北京", d)
        assert a == b  # 完整确定性：同 seed → 同天气

    def test_summer_temperature_range(self):
        # 夏季 baseTemp≈30-(lat-25)*0.3，high/low 应在合理区间
        w = generate_weather_for_date("北京", date(2026, 7, 15))
        assert w["high_temp"] > w["low_temp"]
        assert 15 <= w["low_temp"] <= 35
        assert 20 <= w["high_temp"] <= 45
        assert w["low_temp"] <= w["current_temp"] <= w["high_temp"]

    def test_winter_temperature_range(self):
        w = generate_weather_for_date("哈尔滨", date(2026, 1, 15))
        assert w["high_temp"] > w["low_temp"]
        assert w["low_temp"] < 10  # 北方冬季低温

    def test_unsupported_city_raises(self):
        try:
            generate_weather_for_date("不存在城", date(2026, 8, 23))
            raised = False
        except KeyError:
            raised = True
        assert raised


class TestToolDefinition:
    def test_tool_name(self):
        assert WEATHER_TOOL_NAME == "weather_query"

    def test_input_schema(self):
        definition = build_weather_tool_definition()
        assert definition["name"] == "weather_query"
        schema = definition["input_schema"]
        assert schema["type"] == "object"
        assert "city" in schema["required"]
        props = schema["properties"]
        assert props["city"]["type"] == "string"
        assert props["queryType"]["enum"] == ["current", "forecast"]
        assert props["days"]["default"] == 3


class TestHandleCall:
    def test_current_fields(self):
        text, is_error = handle_weather_call({"city": "北京"})
        assert is_error is False
        for field in ("今日天气", "天气", "温度", "相对湿度", "风向", "风力", "空气质量"):
            assert field in text

    def test_forecast_days_clamp_low(self):
        text, is_error = handle_weather_call({"city": "北京", "queryType": "forecast", "days": 0})
        assert is_error is False
        assert "未来3天" in text

    def test_forecast_days_clamp_high(self):
        text, is_error = handle_weather_call({"city": "北京", "queryType": "forecast", "days": 99})
        assert is_error is False
        assert "未来7天" in text

    def test_unknown_city_is_error(self):
        text, is_error = handle_weather_call({"city": "火星"})
        assert is_error is True
        assert "暂不支持" in text

    def test_missing_city_is_error(self):
        text, is_error = handle_weather_call({})
        assert is_error is True
