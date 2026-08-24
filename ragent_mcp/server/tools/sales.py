# -*- coding: utf-8 -*-
"""
ragent_mcp.server.tools.sales - sales_query MCP 工具（对应 Java SalesMcpExecutor）

独立部署边界（D7）：本模块不 import rag/app/core。

对齐 Java 语义：
    - 数据集逐条照抄（B4）：REGIONS / PRODUCTS / SALES_BY_REGION / CUSTOMER_POOL
    - seed = start.toEpochDay()（Java new Random(start.toEpochDay())），给定日期区间 → 数据稳定；
      Python random.Random(seed) 同 seed 恒稳定（与 Java 序列逐字节不同属预期——Random 算法族不同）
    - 跳过周末（Java getDayOfWeek().getValue() > 5）
    - 金额档位：企业版 50+rand*150 / 专业版 10+rand*40 / 基础版 1+rand*9，half-up 到 2 位
    - 缓存：cacheKey = period + "_" + 今日，同 period 同日不重算
    - 四查询类型：summary / ranking / detail / trend（对齐 Java buildXxxResult 输出格式）
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

SALES_TOOL_NAME = "sales_query"

# 数据集（逐条照抄 Java）
REGIONS = ["华东", "华南", "华北", "西南", "西北"]
PRODUCTS = ["企业版", "专业版", "基础版"]
SALES_BY_REGION = {
    "华东": ["张三", "李四", "王五"],
    "华南": ["赵六", "钱七", "孙八"],
    "华北": ["周九", "吴十", "郑冬"],
    "西南": ["陈春", "林夏", "黄秋"],
    "西北": ["刘一", "杨二", "马三"],
}
CUSTOMER_POOL = [
    "腾讯科技", "阿里巴巴", "字节跳动", "美团点评", "京东集团",
    "百度在线", "网易公司", "小米科技", "华为技术", "中兴通讯",
    "用友网络", "金蝶软件", "浪潮集团", "东软集团", "科大讯飞",
    "三一重工", "中联重科", "格力电器", "美的集团", "海尔智家",
]

# 模块级数据缓存（对齐 Java cachedData/cacheKey 字段）
_cached_data: Optional[List[Dict[str, Any]]] = None
_cache_key: Optional[str] = None

_EPOCH_ORDINAL = 719163  # date(1970,1,1).toordinal()


def _sales_seed(day: date) -> int:
    """seed = epochDay（Java new Random(start.toEpochDay())）"""
    return day.toordinal() - _EPOCH_ORDINAL


def _round2(value: float) -> float:
    """half-up 到 2 位小数（对齐 Java Math.round(v*100)/100.0）"""
    return int(value * 100 + 0.5) / 100.0


def _get_date_range(period: str, now: date) -> tuple:
    """对齐 Java getDateRange：返回 (start, end)"""
    if period == "上月":
        end = date(now.year, now.month, 1) - timedelta(days=1)
        start = date(end.year, end.month, 1)
        return start, end
    if period == "本季度":
        q = (now.month - 1) // 3
        return date(now.year, q * 3 + 1, 1), now
    if period == "上季度":
        q = (now.month - 1) // 3
        end = date(now.year, q * 3 + 1, 1) - timedelta(days=1)
        start_month = ((q - 1) % 4) * 3 + 1
        return date(end.year if start_month <= end.month else end.year - 1, start_month, 1), end
    if period == "本年":
        return date(now.year, 1, 1), now
    return date(now.year, now.month, 1), now


def generate_mock_data(start: date, end: date) -> List[Dict[str, Any]]:
    """对齐 Java generateMockData：逐日生成（跳过周末），seed=start.toEpochDay()"""
    records: List[Dict[str, Any]] = []
    rng = random.Random(_sales_seed(start))
    days = (end - start).days + 1
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:  # Java getDayOfWeek().getValue() > 5（周六=6/周日=7）
            continue
        orders_per_day = 3 + rng.randrange(6)
        for _ in range(orders_per_day):
            region = REGIONS[rng.randrange(len(REGIONS))]
            sales_person = SALES_BY_REGION[region][rng.randrange(3)]
            product = PRODUCTS[rng.randrange(len(PRODUCTS))]
            customer = CUSTOMER_POOL[rng.randrange(len(CUSTOMER_POOL))] + str(day.day)
            if product == "企业版":
                amount = 50 + rng.random() * 150
            elif product == "专业版":
                amount = 10 + rng.random() * 40
            else:
                amount = 1 + rng.random() * 9
            records.append({
                "region": region,
                "sales_person": sales_person,
                "product": product,
                "customer": customer,
                "amount": _round2(amount),
                "date": day.isoformat(),
            })
    return records


def get_or_generate_data(period: str) -> List[Dict[str, Any]]:
    """对齐 Java getOrGenerateData：缓存按 period+今日；同 key 不重算"""
    global _cached_data, _cache_key
    today = datetime.now().date()
    key = f"{period}_{today}"
    if _cached_data is not None and key == _cache_key:
        return _cached_data
    start, end = _get_date_range(period, today)
    _cached_data = generate_mock_data(start, end)
    _cache_key = key
    return _cached_data


def filter_data(data, region: Optional[str], product: Optional[str], sales_person: Optional[str]) -> list:
    return [
        r for r in data
        if (region is None or region == r["region"])
        and (product is None or product == r["product"])
        and (sales_person is None or sales_person == r["sales_person"])
    ]


# ==================== 输出格式化（对齐 Java buildXxxResult） ====================


def _build_summary(data, region, period, product, sales_person) -> str:
    total = sum(r["amount"] for r in data)
    order_count = len(data)
    avg = total / order_count if order_count else 0
    lines = [f"【{period} 销售数据汇总】", ""]
    filters = []
    if region:
        filters.append(f"地区: {region}")
    if product:
        filters.append(f"产品: {product}")
    if sales_person:
        filters.append(f"销售: {sales_person}")
    if filters:
        lines.append("筛选条件: " + "，".join(filters) + "\n")
    lines.append(f"总销售额: ¥{total:.2f} 万")
    lines.append(f"成交订单: {order_count} 笔")
    lines.append(f"平均单价: ¥{avg:.2f} 万")
    if product is None and data:
        by_product: Dict[str, float] = {}
        for r in data:
            by_product[r["product"]] = by_product.get(r["product"], 0) + r["amount"]
        lines.append("")
        lines.append("【按产品分布】")
        for name, amount in sorted(by_product.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name}: ¥{amount:.2f} 万 ({amount / total * 100:.1f}%)")
    if region is None and data:
        by_region: Dict[str, float] = {}
        for r in data:
            by_region[r["region"]] = by_region.get(r["region"], 0) + r["amount"]
        lines.append("")
        lines.append("【按地区分布】")
        for name, amount in sorted(by_region.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name}: ¥{amount:.2f} 万 ({amount / total * 100:.1f}%)")
    return "\n".join(lines).strip()


def _build_ranking(data, region, period, limit) -> str:
    by_sales: Dict[str, float] = {}
    for r in data:
        by_sales[r["sales_person"]] = by_sales.get(r["sales_person"], 0) + r["amount"]
    ranking = sorted(by_sales.items(), key=lambda kv: -kv[1])[:limit]
    header = f"【{period}" + (f" {region}" if region else "") + " 销售排名】"
    lines = [header, ""]
    if not ranking:
        lines.append("暂无销售数据")
    else:
        for i, (name, amount) in enumerate(ranking, 1):
            lines.append(f"第{i}名: {name} - ¥{amount:.2f} 万")
    return "\n".join(lines).strip()


def _build_detail(data, region, period, limit) -> str:
    top = sorted(data, key=lambda r: -r["amount"])[:limit]
    header = f"【{period}" + (f" {region}" if region else "") + " 销售明细】"
    lines = [header, "", f"共 {len(data)} 条记录，显示金额最高的 {len(top)} 条：", ""]
    for i, r in enumerate(top, 1):
        lines.append(f"{i}. {r['customer']}")
        lines.append(f"   产品: {r['product']} | 金额: ¥{r['amount']:.2f} 万")
        lines.append(f"   销售: {r['sales_person']} | 地区: {r['region']} | 日期: {r['date']}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_trend(data, region, period) -> str:
    by_week: Dict[str, float] = {}
    for r in data:
        day = int(r["date"][-2:])
        week = (day - 1) // 7 + 1
        key = f"第{week}周"
        by_week[key] = by_week.get(key, 0) + r["amount"]
    header = f"【{period}" + (f" {region}" if region else "") + " 销售趋势】"
    lines = [header, ""]
    if not by_week:
        lines.append("暂无数据")
    else:
        for key in sorted(by_week):
            lines.append(f"{key}: ¥{by_week[key]:.2f} 万")
    return "\n".join(lines).strip()


# ==================== 工具声明 + 调用处理 ====================


def build_sales_tool_definition() -> Dict[str, Any]:
    return {
        "name": SALES_TOOL_NAME,
        "description": "查询软件销售数据，支持按地区、时间、产品、销售人员等维度筛选，支持汇总统计、排名、明细列表等多种查询",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "地区筛选：华东、华南、华北、西南、西北，不填则查询全国", "enum": REGIONS},
                "period": {"type": "string", "description": "时间段：本月、上月、本季度、上季度、本年，默认本月", "enum": ["本月", "上月", "本季度", "上季度", "本年"], "default": "本月"},
                "product": {"type": "string", "description": "产品筛选：企业版、专业版、基础版，不填则查询全部产品", "enum": PRODUCTS},
                "salesPerson": {"type": "string", "description": "销售人员姓名，不填则查询全部销售"},
                "queryType": {"type": "string", "description": "查询类型：summary(汇总)、ranking(排名)、detail(明细)、trend(趋势)", "enum": ["summary", "ranking", "detail", "trend"], "default": "summary"},
                "limit": {"type": "integer", "description": "返回记录数限制，默认10", "default": 10},
            },
            "required": [],
        },
    }


def handle_sales_call(arguments: Dict[str, Any]) -> Tuple[str, bool]:
    """处理 sales_query 调用 → (text, is_error)；对齐 Java handleCall 参数校验"""
    try:
        region = _str_or_none(arguments.get("region"))
        period = _str_or_none(arguments.get("period")) or "本月"
        product = _str_or_none(arguments.get("product"))
        sales_person = _str_or_none(arguments.get("salesPerson"))
        query_type = _str_or_none(arguments.get("queryType")) or "summary"
        limit = _int_or_none(arguments.get("limit")) or 10
        if limit <= 0:
            limit = 10

        all_data = get_or_generate_data(period)
        filtered = filter_data(all_data, region, product, sales_person)

        if query_type == "ranking":
            text = _build_ranking(filtered, region, period, limit)
        elif query_type == "detail":
            text = _build_detail(filtered, region, period, limit)
        elif query_type == "trend":
            text = _build_trend(filtered, region, period)
        else:
            text = _build_summary(filtered, region, period, product, sales_person)
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
