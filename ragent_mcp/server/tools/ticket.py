# -*- coding: utf-8 -*-
"""
ragent_mcp.server.tools.ticket - ticket_query MCP 工具（对应 Java TicketMcpExecutor）

独立部署边界（D7）：本模块不 import rag/app/core。

对齐 Java 语义：
    - 数据集逐条照抄（B4）：REGIONS / PRODUCTS / CUSTOMERS_BY_REGION / ENGINEERS_BY_REGION /
      ISSUE_TEMPLATES（15 条）/ CATEGORIES（6 类）/ STATUSES / PRIORITIES
    - seed = today.toEpochDay()（Java new Random(today.toEpochDay())），给定日期 → 数据稳定
    - 近 30 天生成（跳过周末）；ticketId 格式 TK-yyyyMM-NNNN
    - 优先级权重：<5 紧急 / <20 高 / <60 中 / 其余 低
    - 状态按 d 分三段：>7 天 80% 已关闭 / 3-7 天混合 / <=3 天 35% 待处理
    - 缓存：cacheKey = tickets_ + 今日
    - 三查询类型：summary / list / stats（对齐 Java buildXxxResult 输出格式）
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

TICKET_TOOL_NAME = "ticket_query"

# 数据集（逐条照抄 Java）
REGIONS = ["华东", "华南", "华北", "西南", "西北"]
PRODUCTS = ["企业版", "专业版", "基础版"]
STATUS_PENDING = "待处理"
STATUS_IN_PROGRESS = "处理中"
STATUS_RESOLVED = "已解决"
STATUS_CLOSED = "已关闭"
STATUSES = [STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_CLOSED]
PRIORITIES = ["紧急", "高", "中", "低"]
CATEGORIES = ["功能异常", "性能问题", "安装部署", "使用咨询", "数据问题", "权限问题"]

CUSTOMERS_BY_REGION = {
    "华东": ["腾讯科技", "阿里巴巴", "字节跳动", "网易公司"],
    "华南": ["美团点评", "京东集团", "小米科技", "格力电器"],
    "华北": ["百度在线", "华为技术", "中兴通讯", "用友网络"],
    "西南": ["科大讯飞", "金蝶软件", "三一重工", "中联重科"],
    "西北": ["浪潮集团", "东软集团", "美的集团", "海尔智家"],
}
ENGINEERS_BY_REGION = {
    "华东": ["工程师A1", "工程师A2"],
    "华南": ["工程师B1", "工程师B2"],
    "华北": ["工程师C1", "工程师C2"],
    "西南": ["工程师D1", "工程师D2"],
    "西北": ["工程师E1", "工程师E2"],
}
ISSUE_TEMPLATES = [
    "系统登录后页面白屏无法操作",
    "报表导出功能超时失败",
    "用户权限配置不生效",
    "数据同步延迟超过预期",
    "批量导入数据格式校验异常",
    "API接口调用返回500错误",
    "定时任务未按计划执行",
    "搜索功能结果不准确",
    "通知消息无法正常推送",
    "文件上传大小限制配置无效",
    "仪表盘数据展示不一致",
    "多租户数据隔离存在问题",
    "审批流程节点卡住无法流转",
    "移动端页面适配显示异常",
    "数据备份任务执行失败",
]

_cached_data: Optional[List[Dict[str, Any]]] = None
_cache_key: Optional[str] = None

_EPOCH_ORDINAL = 719163


def _ticket_seed(day: date) -> int:
    """seed = epochDay（Java new Random(today.toEpochDay())）"""
    return day.toordinal() - _EPOCH_ORDINAL


def generate_mock_data(today: date) -> List[Dict[str, Any]]:
    """对齐 Java generateMockData：近 30 天生成（跳过周末），seed=today.toEpochDay()"""
    records: List[Dict[str, Any]] = []
    rng = random.Random(_ticket_seed(today))
    ticket_seq = 1
    year_month = today.strftime("%Y%m")

    for d in range(30):
        day = today - timedelta(days=d)
        if day.weekday() >= 5:
            continue
        tickets_per_day = 2 + rng.randrange(5)
        for _ in range(tickets_per_day):
            region = REGIONS[rng.randrange(len(REGIONS))]
            customer = CUSTOMERS_BY_REGION[region][rng.randrange(4)]
            product = PRODUCTS[rng.randrange(len(PRODUCTS))]
            title = ISSUE_TEMPLATES[rng.randrange(len(ISSUE_TEMPLATES))]
            category = CATEGORIES[rng.randrange(len(CATEGORIES))]
            engineer = ENGINEERS_BY_REGION[region][rng.randrange(2)]

            priority_weight = rng.randrange(100)
            if priority_weight < 5:
                priority = "紧急"
            elif priority_weight < 20:
                priority = "高"
            elif priority_weight < 60:
                priority = "中"
            else:
                priority = "低"

            if d > 7:
                status = STATUS_CLOSED if rng.randrange(100) < 80 else STATUS_RESOLVED
            elif d > 3:
                sw = rng.randrange(100)
                if sw < 30:
                    status = STATUS_RESOLVED
                elif sw < 60:
                    status = STATUS_CLOSED
                elif sw < 85:
                    status = STATUS_IN_PROGRESS
                else:
                    status = STATUS_PENDING
            else:
                sw = rng.randrange(100)
                if sw < 35:
                    status = STATUS_PENDING
                elif sw < 70:
                    status = STATUS_IN_PROGRESS
                elif sw < 90:
                    status = STATUS_RESOLVED
                else:
                    status = STATUS_CLOSED

            records.append({
                "ticket_id": f"TK-{year_month}-{ticket_seq:04d}",
                "region": region,
                "customer": customer,
                "product": product,
                "title": title,
                "category": category,
                "priority": priority,
                "status": status,
                "engineer": engineer,
                "create_date": day.isoformat(),
            })
            ticket_seq += 1
    return records


def get_or_generate_data() -> List[Dict[str, Any]]:
    """对齐 Java getOrGenerateData：缓存按 tickets_+今日；同 key 不重算"""
    global _cached_data, _cache_key
    today = datetime.now().date()
    key = f"tickets_{today}"
    if _cached_data is not None and key == _cache_key:
        return _cached_data
    _cached_data = generate_mock_data(today)
    _cache_key = key
    return _cached_data


def filter_data(data, region, status, priority, product, customer_name) -> list:
    return [
        t for t in data
        if (region is None or region == t["region"])
        and (status is None or status == t["status"])
        and (priority is None or priority == t["priority"])
        and (product is None or product == t["product"])
        and (customer_name is None or customer_name in t["customer"])
    ]


# ==================== 输出格式化（对齐 Java buildXxxResult） ====================


def _build_summary(data, region, status, priority, product) -> str:
    total = len(data)
    pending = sum(1 for t in data if t["status"] == STATUS_PENDING)
    in_progress = sum(1 for t in data if t["status"] == STATUS_IN_PROGRESS)
    resolved = sum(1 for t in data if t["status"] == STATUS_RESOLVED)
    closed = sum(1 for t in data if t["status"] == STATUS_CLOSED)
    urgent = sum(1 for t in data if t["priority"] == "紧急")
    high = sum(1 for t in data if t["priority"] == "高")

    lines = ["【客户工单汇总概览】", ""]
    filters = []
    if region:
        filters.append(f"地区: {region}")
    if status:
        filters.append(f"状态: {status}")
    if priority:
        filters.append(f"优先级: {priority}")
    if product:
        filters.append(f"产品: {product}")
    if filters:
        lines.append("筛选条件: " + "，".join(filters) + "\n")

    lines.append(f"工单总数: {total} 个")
    lines.append("")
    lines.append("【状态分布】")
    lines.append(f"  待处理: {pending} 个")
    lines.append(f"  处理中: {in_progress} 个")
    lines.append(f"  已解决: {resolved} 个")
    lines.append(f"  已关闭: {closed} 个")
    if total > 0:
        lines.append("")
        lines.append(f"解决率: {(resolved + closed) * 100.0 / total:.1f}%")
    if urgent + high > 0:
        lines.append("")
        lines.append(f"⚠ 紧急/高优先级工单: {urgent + high} 个（紧急 {urgent}，高 {high}）")

    if product is None and data:
        by_product: Dict[str, int] = {}
        for t in data:
            by_product[t["product"]] = by_product.get(t["product"], 0) + 1
        lines.append("")
        lines.append("【按产品分布】")
        for name, count in sorted(by_product.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name}: {count} 个")

    if region is None and data:
        by_region: Dict[str, int] = {}
        for t in data:
            by_region[t["region"]] = by_region.get(t["region"], 0) + 1
        lines.append("")
        lines.append("【按地区分布】")
        for name, count in sorted(by_region.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name}: {count} 个")

    return "\n".join(lines).strip()


def _build_list(data, limit) -> str:
    sorted_data = sorted(
        data,
        key=lambda t: (PRIORITIES.index(t["priority"]), t["create_date"]),
        reverse=False,
    )
    top = sorted_data[:limit]
    lines = [f"【工单列表】共 {len(data)} 条，显示 {len(top)} 条（按优先级排序）", ""]
    for i, t in enumerate(top, 1):
        lines.append(f"{i}. [{t['ticket_id']}] {t['title']}")
        lines.append(f"   客户: {t['customer']} | 产品: {t['product']} | 地区: {t['region']}")
        lines.append(f"   优先级: {t['priority']} | 状态: {t['status']} | 分类: {t['category']}")
        lines.append(f"   处理人: {t['engineer']} | 创建时间: {t['create_date']}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_stats(data) -> str:
    lines = ["【工单统计分析】", ""]
    if not data:
        lines.append("暂无工单数据")
        return "\n".join(lines).strip()

    by_category: Dict[str, int] = {}
    for t in data:
        by_category[t["category"]] = by_category.get(t["category"], 0) + 1
    lines.append("【问题分类统计】")
    for name, count in sorted(by_category.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {name}: {count} 个 ({count * 100.0 / len(data):.1f}%)")

    lines.append("")
    lines.append("【各产品解决率】")
    by_product: Dict[str, list] = {}
    for t in data:
        by_product.setdefault(t["product"], []).append(t)
    for product, tickets in by_product.items():
        resolved_count = sum(1 for t in tickets if t["status"] in (STATUS_RESOLVED, STATUS_CLOSED))
        lines.append(f"  {product}: {resolved_count * 100.0 / len(tickets):.1f}% ({resolved_count}/{len(tickets)})")

    lines.append("")
    lines.append("【处理人工单量排名】")
    by_engineer: Dict[str, int] = {}
    for t in data:
        if t["status"] in (STATUS_PENDING, STATUS_IN_PROGRESS):
            by_engineer[t["engineer"]] = by_engineer.get(t["engineer"], 0) + 1
    for name, count in sorted(by_engineer.items(), key=lambda kv: -kv[1])[:5]:
        lines.append(f"  {name}: {count} 个待处理")

    return "\n".join(lines).strip()


# ==================== 工具声明 + 调用处理 ====================


def build_ticket_tool_definition() -> Dict[str, Any]:
    return {
        "name": TICKET_TOOL_NAME,
        "description": "查询客户技术支持工单数据，支持按地区、状态、优先级、产品、客户等维度筛选，支持汇总概览、工单列表、统计分析等多种查询",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "地区筛选：华东、华南、华北、西南、西北，不填则查询全国", "enum": REGIONS},
                "status": {"type": "string", "description": "工单状态筛选：待处理、处理中、已解决、已关闭，不填则查询全部状态", "enum": STATUSES},
                "priority": {"type": "string", "description": "优先级筛选：紧急、高、中、低，不填则查询全部优先级", "enum": PRIORITIES},
                "product": {"type": "string", "description": "产品筛选：企业版、专业版、基础版，不填则查询全部产品", "enum": PRODUCTS},
                "customerName": {"type": "string", "description": "客户名称关键字，支持模糊匹配"},
                "queryType": {"type": "string", "description": "查询类型：summary(汇总概览)、list(工单列表)、stats(统计分析)", "enum": ["summary", "list", "stats"], "default": "summary"},
                "limit": {"type": "integer", "description": "返回记录数限制，默认10", "default": 10},
            },
            "required": [],
        },
    }


def handle_ticket_call(arguments: Dict[str, Any]) -> Tuple[str, bool]:
    """处理 ticket_query 调用 → (text, is_error)；对齐 Java handleCall 参数校验"""
    try:
        region = _str_or_none(arguments.get("region"))
        status = _str_or_none(arguments.get("status"))
        priority = _str_or_none(arguments.get("priority"))
        product = _str_or_none(arguments.get("product"))
        customer_name = _str_or_none(arguments.get("customerName"))
        query_type = _str_or_none(arguments.get("queryType")) or "summary"
        limit = _int_or_none(arguments.get("limit")) or 10
        if limit <= 0:
            limit = 10

        all_data = get_or_generate_data()
        filtered = filter_data(all_data, region, status, priority, product, customer_name)

        if query_type == "list":
            text = _build_list(filtered, limit)
        elif query_type == "stats":
            text = _build_stats(filtered)
        else:
            text = _build_summary(filtered, region, status, priority, product)
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
