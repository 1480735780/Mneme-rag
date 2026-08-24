# -*- coding: utf-8 -*-
"""
ragent_mcp.server.tools.weather - weather_query MCP 工具（对应 Java WeatherMcpExecutor）

独立部署边界（D7）：本模块不 import rag/app/core 任何模块，可随 mcp-server 独立进程运行。

对齐 Java 语义：
    - 20 城市坐标表逐条照抄（CITY_COORDINATES）；
    - 四季天气类型枚举照抄（SPRING/SUMMER/AUTUMN/WINTER）；
    - seed = epochDay * 31 + java_hash(city)（B1：java_hash 为移植 Java String.hashCode()
      31 多项式 + 32 位有符号回绕，跨进程永稳；严禁 Python 内置 hash()——PYTHONHASHSEED 盐跨进程漂移）；
    - 按季节 + 纬度推 baseTemp，Random(seed) 生成 high/low/current 温度、天气类型、湿度、
      风向/风力、AQI 等级（B1 要求 seed 确定性，Python random.Random(seed) 同 seed 恒稳定；
      与 Java 输出逐字节不同属预期——Random 算法族不同）；
    - current / forecast 双查询类型，days 钳制 3-7；出行提示（降水/高温/低温）。

输出：(text, is_error) 二元组，由 main.py 包成 MCP CallToolResult（isError）。
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Tuple

WEATHER_TOOL_NAME = "weather_query"

# 20 城市坐标表（逐条照抄 Java WeatherMcpExecutor.CITY_COORDINATES）
CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
    "北京": (39.9, 116.4),
    "上海": (31.2, 121.5),
    "广州": (23.1, 113.3),
    "深圳": (22.5, 114.1),
    "杭州": (30.3, 120.2),
    "成都": (30.6, 104.1),
    "武汉": (30.6, 114.3),
    "南京": (32.1, 118.8),
    "西安": (34.3, 108.9),
    "重庆": (29.6, 106.5),
    "长沙": (28.2, 112.9),
    "天津": (39.1, 117.2),
    "苏州": (31.3, 120.6),
    "郑州": (34.7, 113.6),
    "青岛": (36.1, 120.4),
    "大连": (38.9, 121.6),
    "厦门": (24.5, 118.1),
    "昆明": (25.0, 102.7),
    "哈尔滨": (45.8, 126.5),
    "三亚": (18.3, 109.5),
}

# 四季天气类型（照抄 Java）
_WEATHER_TYPES_SPRING = ("晴", "多云", "阴", "小雨", "阵雨", "多云转晴")
_WEATHER_TYPES_SUMMER = ("晴", "多云", "雷阵雨", "大雨", "暴雨", "多云转阴")
_WEATHER_TYPES_AUTUMN = ("晴", "多云", "阴", "小雨", "晴转多云", "多云转晴")
_WEATHER_TYPES_WINTER = ("晴", "多云", "阴", "小雪", "中雪", "晴转多云", "雾")

_WIND_DIRECTIONS = ("东风", "南风", "西风", "北风", "东南风", "西北风", "东北风", "西南风")

# Java 时代基数（Python date.toordinal() 的 1970-01-01 对应值：date(1970,1,1).toordinal() == 719163）
_EPOCH_ORDINAL = 719163


# ==================== seed 确定性（B1 核心） ====================


def _java_hash_code(text: str) -> int:
    """移植 Java String.hashCode()：31 多项式 + 32 位有符号回绕，跨进程永稳

    Java: h = 0; for (char c : s) h = 31*h + c; 返回 int（溢出自然回绕为 32 位有符号）
    """
    h = 0
    for ch in text:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return h


def _epoch_day(day: date) -> int:
    """Java LocalDate.toEpochDay() 等价（1970-01-01 → 0）"""
    return day.toordinal() - _EPOCH_ORDINAL


def _weather_seed(city: str, day: date) -> int:
    """seed = epochDay * 31 + java_hash(city)（照抄 Java WeatherMcpExecutor.generateWeatherForDate）"""
    return _epoch_day(day) * 31 + _java_hash_code(city)


# ==================== 天气生成（对齐 Java generateWeatherForDate） ====================


def _season(month: int) -> int:
    """季节索引：春 0 / 夏 1 / 秋 2 / 冬 3（照抄 Java month 判定）"""
    if 3 <= month <= 5:
        return 0
    if 6 <= month <= 8:
        return 1
    if 9 <= month <= 11:
        return 2
    return 3


def _season_weather_types(season: int) -> Tuple[str, ...]:
    if season == 0:
        return _WEATHER_TYPES_SPRING
    if season == 1:
        return _WEATHER_TYPES_SUMMER
    if season == 2:
        return _WEATHER_TYPES_AUTUMN
    return _WEATHER_TYPES_WINTER


def generate_weather_for_date(city: str, day: date) -> Dict[str, Any]:
    """生成某日天气（确定性：同 city + day → 同结果）；未知城市抛 KeyError

    对齐 Java generateWeatherForDate 的温度/湿度/AQI 推演（Python random.Random 同 seed 稳定）。
    """
    lat, _lon = CITY_COORDINATES[city]
    seed = _weather_seed(city, day)
    rng = random.Random(seed)

    season = _season(day.month)
    if season == 0:
        base_temp = 15 - (lat - 25) * 0.5
    elif season == 1:
        base_temp = 30 - (lat - 25) * 0.3
    elif season == 2:
        base_temp = 18 - (lat - 25) * 0.5
    else:
        base_temp = 5 - (lat - 25) * 0.8

    high_temp = int(base_temp + 3 + rng.randint(0, 5))
    low_temp = int(base_temp - 3 - rng.randint(0, 4))
    current_temp = low_temp + rng.randint(0, max(1, high_temp - low_temp))

    weather_type = rng.choice(_season_weather_types(season))

    if season == 1:
        humidity = 60 + rng.randint(0, 29)
    elif season == 3:
        humidity = 20 + rng.randint(0, 29)
    else:
        humidity = 40 + rng.randint(0, 29)
    if "雨" in weather_type or "雪" in weather_type:
        humidity = min(95, humidity + 20)

    wind_direction = rng.choice(_WIND_DIRECTIONS)
    wind_force = 1 + rng.randint(0, 4)
    wind_level = f"{wind_force}-{wind_force + 1}级"

    aqi_base = 30 + rng.randint(0, 119)
    if lat > 35:
        aqi_base += 20
    if aqi_base <= 50:
        air_quality = "优"
    elif aqi_base <= 100:
        air_quality = "良"
    elif aqi_base <= 150:
        air_quality = "轻度污染"
    else:
        air_quality = "中度污染"

    return {
        "weather_type": weather_type,
        "current_temp": current_temp,
        "high_temp": high_temp,
        "low_temp": low_temp,
        "humidity": humidity,
        "wind_direction": wind_direction,
        "wind_level": wind_level,
        "air_quality": air_quality,
    }


# ==================== 输出格式化（对齐 Java buildCurrentResult / buildForecastResult） ====================


def _build_current_result(city: str, day: date) -> str:
    w = generate_weather_for_date(city, day)
    lines = [
        f"【{city} 今日天气】",
        "",
        f"日期: {day.strftime('%Y年%m月%d日')}",
        f"天气: {w['weather_type']}",
        f"当前温度: {w['current_temp']}°C",
        f"最高温度: {w['high_temp']}°C",
        f"最低温度: {w['low_temp']}°C",
        f"相对湿度: {w['humidity']}%",
        f"风向: {w['wind_direction']}",
        f"风力: {w['wind_level']}",
        f"空气质量: {w['air_quality']}",
    ]
    if "雨" in w["weather_type"] or "雪" in w["weather_type"]:
        lines.append("")
        lines.append("提示: 今日有降水，出行请携带雨具。")
    elif w["high_temp"] >= 35:
        lines.append("")
        lines.append("提示: 今日高温，注意防暑降温。")
    elif w["low_temp"] <= 0:
        lines.append("")
        lines.append("提示: 今日气温较低，注意防寒保暖。")
    return "\n".join(lines).strip()


def _build_forecast_result(city: str, day: date, days: int) -> str:
    lines = [f"【{city} 未来{days}天天气预报】", ""]
    for offset in range(days):
        target = day + timedelta(days=offset)
        w = generate_weather_for_date(city, target)
        day_label = "今天" if offset == 0 else ("明天" if offset == 1 else ("后天" if offset == 2 else target.strftime("%m月%d日")))
        lines.append(f"📅 {day_label}（{target.strftime('%m-%d')}）")
        lines.append(f"   天气: {w['weather_type']} | 温度: {w['low_temp']}°C ~ {w['high_temp']}°C")
        lines.append(f"   湿度: {w['humidity']}% | {w['wind_direction']} {w['wind_level']}")
        lines.append("")
    today = generate_weather_for_date(city, day)
    last_day = generate_weather_for_date(city, day + timedelta(days=days - 1))
    temp_trend = last_day["high_temp"] - today["high_temp"]
    if abs(temp_trend) >= 5:
        trend_text = "逐渐升高" if temp_trend > 0 else "逐渐下降"
        advice = "防暑" if temp_trend > 0 else "保暖"
        lines.append(f"趋势: 未来{days}天气温{trend_text}，注意{advice}。")
    return "\n".join(lines).strip()


# ==================== 工具声明 + 调用处理 ====================


def build_weather_tool_definition() -> Dict[str, Any]:
    """工具声明（对齐 Java buildTool：name/description/inputSchema）"""
    return {
        "name": WEATHER_TOOL_NAME,
        "description": "查询城市天气信息，支持查看当前实时天气和未来多天天气预报，包含温度、湿度、风力、天气状况等信息",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称，如北京、上海、广州等"},
                "queryType": {
                    "type": "string",
                    "description": "查询类型：current(当前天气)、forecast(未来预报)",
                    "enum": ["current", "forecast"],
                    "default": "current",
                },
                "days": {"type": "integer", "description": "预报天数，仅forecast模式有效，默认3天，最多7天", "default": 3},
            },
            "required": ["city"],
        },
    }


def handle_weather_call(arguments: Dict[str, Any]) -> Tuple[str, bool]:
    """处理 weather_query 调用 → (text, is_error)；对齐 Java handleCall 的参数校验与异常兜底"""
    city = arguments.get("city")
    query_type = arguments.get("queryType") or "current"
    days = arguments.get("days")

    if not city or not str(city).strip():
        return "请提供城市名称", True
    city = str(city).strip()
    if query_type is None or not str(query_type).strip():
        query_type = "current"
    try:
        days = int(days) if days is not None else 3
    except (TypeError, ValueError):
        days = 3
    if days <= 0:
        days = 3
    if days > 7:
        days = 7

    if city not in CITY_COORDINATES:
        return f"暂不支持查询该城市，当前支持：{'、'.join(CITY_COORDINATES)}", True

    today = datetime.now().date()
    if query_type == "forecast":
        return _build_forecast_result(city, today, days), False
    return _build_current_result(city, today), False
