# -*- coding: utf-8 -*-
"""
ragent_mcp.server.tools.asset - asset_query MCP 工具（对应 Java AssetMcpExecutor，v1.1 审计 R-A 移植）

独立部署边界（D7）：本模块不 import rag/app/core。

对齐 Java 语义：
    - 数据集逐条照抄：CATEGORIES / STATUSES / SERVICE_LIMIT_MONTHS / CATEGORY_CODES / MODELS / LOCATIONS
    - seed = Java String.hashCode(employeeName)（Python hash() 进程内随机化，须自实现 32 位 Java hash）
      Python random.Random(seed) 与 Java Random 序列不同属预期（同 sales 工具口径），但同 seed 恒稳定
    - 每人数据按天缓存（cacheKey = employeeName + "_" + 今日）
    - 三查询类型：summary / list / renewal（对齐 Java buildXxxResult 输出格式）
    - 服役年限判定：servedMonths >= serviceLimitMonths（笔记本电脑 48 月等，逐类照抄）
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

ASSET_TOOL_NAME = "asset_query"

DEFAULT_EMPLOYEE = "张三"

CATEGORIES = ["笔记本电脑", "台式机", "显示器", "扩展坞", "移动硬盘", "测试手机"]
STATUSES = ["在用", "维修中", "借用中", "待归还"]

SERVICE_LIMIT_MONTHS = {
    "笔记本电脑": 48,
    "台式机": 60,
    "显示器": 72,
    "扩展坞": 36,
    "移动硬盘": 36,
    "测试手机": 36,
}
CATEGORY_CODES = {
    "笔记本电脑": "NB",
    "台式机": "PC",
    "显示器": "MT",
    "扩展坞": "DK",
    "移动硬盘": "HD",
    "测试手机": "MP",
}
MODELS = {
    "笔记本电脑": ["ThinkPad X1 Carbon 32G/1T", "MacBook Pro 14 32G/1T", "Dell XPS 15 32G/1T"],
    "台式机": ["Dell OptiPlex 7010 16G/512G", "Lenovo ThinkCentre M720 16G/512G"],
    "显示器": ["Dell U2723QE 27 寸", "LG 27UP850 27 寸", "AOC Q24V4 24 寸"],
    "扩展坞": ["Dell WD19S 130W", "Lenovo ThinkPad Hybrid Dock"],
    "移动硬盘": ["Samsung T7 1T", "WD Elements 2T"],
    "测试手机": ["小米 13 测试机", "华为 Mate 60 测试机", "iPhone 14 测试机"],
}
LOCATIONS = ["西溪园区 A 楼 7F", "西溪园区 B 楼 3F", "紫金港园区 C 楼 5F", "居家办公"]


@dataclass
class AssetRecord:
    asset_no: str
    category: str
    model: str
    status: str
    receive_date: str
    location: str
    service_limit_months: int

    def served_months(self, today: date) -> int:
        received = date.fromisoformat(self.receive_date)
        months = (today.year - received.year) * 12 + today.month - received.month
        if today.day < received.day:
            months -= 1
        return months

    def reached_service_limit(self, today: date) -> bool:
        return self.served_months(today) >= self.service_limit_months


# 模块级数据缓存（对齐 Java cachedData/cacheKey 字段）
_cached_data: Optional[List[AssetRecord]] = None
_cache_key: Optional[str] = None


def java_string_hash(text: str) -> int:
    """Java String.hashCode()：h = 31*h + char，32 位有符号溢出（Python hash() 进程内随机不可用）"""
    h = 0
    for ch in text:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    return h


def _minus_months(today: date, months: int) -> date:
    """Java LocalDate.minusMonths：年回退 + 日 clamp 到当月最后一天"""
    import calendar

    month = today.month - months
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _new_record(rng: random.Random, category: str, served_months: int, status: str, location: str, today: date) -> AssetRecord:
    models = MODELS[category]
    # Java: LocalDate.now().minusMonths(servedMonths).minusDays(random.nextInt(28))
    receive = _minus_months(today, served_months) - timedelta(days=rng.randrange(28))
    return AssetRecord(
        asset_no=f"IT-{CATEGORY_CODES[category]}-{receive.year}-{1 + rng.randrange(9999):04d}",
        category=category,
        model=models[rng.randrange(len(models))],
        status=status,
        receive_date=receive.isoformat(),
        location=location,
        service_limit_months=SERVICE_LIMIT_MONTHS[category],
    )


def _generate_mock_data(employee_name: str, today: date) -> List[AssetRecord]:
    rng = random.Random(java_string_hash(employee_name))
    location = LOCATIONS[rng.randrange(len(LOCATIONS))]

    records = [
        _new_record(rng, "笔记本电脑", 49 + rng.randrange(11), "在用", location, today),
        _new_record(rng, "显示器", 18 + rng.randrange(24), "在用", location, today),
        _new_record(rng, "扩展坞", 18 + rng.randrange(15), "在用", location, today),
    ]
    if rng.random() < 0.5:  # Java nextBoolean()
        records.append(_new_record(rng, "移动硬盘", 38 + rng.randrange(12), "在用", location, today))
    if rng.random() < 0.5:
        records.append(_new_record(rng, "测试手机", 8 + rng.randrange(20), "借用中", location, today))
    records.sort(key=lambda r: r.receive_date)
    return records


def get_or_generate_data(employee_name: str, today: Optional[date] = None) -> List[AssetRecord]:
    """对齐 Java getOrGenerateData：缓存按 employeeName+今日；同 key 不重算"""
    global _cached_data, _cache_key
    today = today or date.today()
    key = f"{employee_name}_{today}"
    if _cached_data is not None and key == _cache_key:
        return _cached_data
    _cached_data = _generate_mock_data(employee_name, today)
    _cache_key = key
    return _cached_data


def _filter_data(data: List[AssetRecord], asset_type: Optional[str], status: Optional[str]) -> List[AssetRecord]:
    return [
        r for r in data
        if (asset_type is None or asset_type == r.category)
        and (status is None or status == r.status)
    ]


def _describe_months(months: int) -> str:
    years, rest = divmod(months, 12)
    if years == 0:
        return f"{rest} 个月"
    if rest == 0:
        return f"{years} 年"
    return f"{years} 年 {rest} 个月"


def _filters_line(asset_type: Optional[str], status: Optional[str]) -> str:
    filters = []
    if asset_type:
        filters.append(f"类别: {asset_type}")
    if status:
        filters.append(f"状态: {status}")
    return f"筛选条件: {'，'.join(filters)}\n\n" if filters else ""


# ==================== 输出格式化（对齐 Java buildXxxResult） ====================


def _build_summary(data: List[AssetRecord], employee_name: str, asset_type: Optional[str],
                   status: Optional[str], today: date) -> str:
    lines = [f"【{employee_name} 名下 IT 资产汇总】", ""]
    filters = _filters_line(asset_type, status)
    if filters:
        lines.append(filters.rstrip("\n"))
    if not data:
        lines.append("名下暂无符合条件的资产记录")
        return "\n".join(lines).strip()

    lines.append(f"资产总数: {len(data)} 件")

    by_status: Dict[str, int] = {}
    for r in data:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    lines.append("状态分布: " + "，".join(f"{k} {v} 件" for k, v in sorted(by_status.items())))

    lines.append("")
    lines.append("【按类别】")
    by_category: Dict[str, int] = {}
    for r in data:
        by_category[r.category] = by_category.get(r.category, 0) + 1
    for name, count in sorted(by_category.items()):
        lines.append(f"  {name}: {count} 件")

    lines.append("")
    lines.append("【资产清单】")
    for r in data:
        lines.append(f"  {r.asset_no} | {r.category} | {r.status} | 领用于 {r.receive_date} | 已服役 {_describe_months(r.served_months(today))}")

    renewable = [r for r in data if r.reached_service_limit(today)]
    lines.append("")
    lines.append("【换新提示】")
    if not renewable:
        lines.append("  名下资产均未达到服役年限")
    else:
        joined = "、".join(f"{r.category} {r.asset_no}" for r in renewable)
        lines.append(f"  已达服役年限: {len(renewable)} 件（{joined}）")
    return "\n".join(lines).strip()


def _build_list(data: List[AssetRecord], employee_name: str, asset_type: Optional[str],
                status: Optional[str], limit: int, today: date) -> str:
    lines = [f"【{employee_name} 名下 IT 资产明细】", ""]
    filters = _filters_line(asset_type, status)
    if filters:
        lines.append(filters.rstrip("\n"))
    if not data:
        lines.append("名下暂无符合条件的资产记录")
        return "\n".join(lines).strip()

    records = data[:limit]
    lines.append(f"共 {len(data)} 件，显示 {len(records)} 件：")
    lines.append("")
    for i, r in enumerate(records, 1):
        reached = "（已达）" if r.reached_service_limit(today) else ""
        lines.append(f"{i}. {r.asset_no} | {r.category}")
        lines.append(f"   机型: {r.model} | 状态: {r.status}")
        lines.append(f"   领用日期: {r.receive_date} | 已服役: {_describe_months(r.served_months(today))} | 服役年限: {r.service_limit_months // 12} 年{reached}")
        lines.append(f"   使用地点: {r.location}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_renewal(data: List[AssetRecord], employee_name: str, today: date) -> str:
    lines = [f"【{employee_name} 资产换新资格检查】", ""]
    if not data:
        lines.append("名下暂无资产记录")
        return "\n".join(lines).strip()
    for r in data:
        served = r.served_months(today)
        lines.append(f"{r.asset_no} | {r.category} | {r.model}")
        if r.reached_service_limit(today):
            lines.append(f"   已服役 {_describe_months(served)}，服役年限 {r.service_limit_months // 12} 年，已达年限，可提交换新工单")
        else:
            lines.append(f"   已服役 {_describe_months(served)}，服役年限 {r.service_limit_months // 12} 年，距可申请还有 {r.service_limit_months - served} 个月")
    lines.append("")
    lines.append("本结果仅为年限判定，换新还需本人提交工单并经直属经理审批")
    return "\n".join(lines).strip()


# ==================== 工具声明 + 调用处理 ====================


def build_asset_tool_definition() -> Dict[str, Any]:
    return {
        "name": ASSET_TOOL_NAME,
        "description": "查询员工名下的公司 IT 资产，包括笔记本电脑、台式机、显示器、扩展坞等，支持按类别和状态筛选，可返回资产汇总、资产明细以及是否达到换新年限",
        "input_schema": {
            "type": "object",
            "properties": {
                "employeeName": {"type": "string", "description": "员工姓名或工号，不填则查询当前登录员工"},
                "assetType": {"type": "string", "description": "资产类别筛选：笔记本电脑、台式机、显示器、扩展坞、移动硬盘、测试手机，不填则查询全部类别", "enum": CATEGORIES},
                "status": {"type": "string", "description": "资产状态筛选：在用、维修中、借用中、待归还，不填则查询全部状态", "enum": STATUSES},
                "queryType": {"type": "string", "description": "查询类型：summary(名下资产汇总)、list(资产明细)、renewal(换新资格检查)", "enum": ["summary", "list", "renewal"], "default": "summary"},
                "limit": {"type": "integer", "description": "返回记录数限制，默认20", "default": 20},
            },
            "required": [],
        },
    }


def handle_asset_call(arguments: Dict[str, Any]) -> Tuple[str, bool]:
    """处理 asset_query 调用 → (text, is_error)；对齐 Java handleCall 参数校验"""
    try:
        employee_name = _str_or_none(arguments.get("employeeName")) or DEFAULT_EMPLOYEE
        asset_type = _str_or_none(arguments.get("assetType"))
        status = _str_or_none(arguments.get("status"))
        query_type = _str_or_none(arguments.get("queryType")) or "summary"
        limit = _int_or_none(arguments.get("limit"))
        if limit is None or limit <= 0:
            limit = 20

        today = date.today()
        all_data = get_or_generate_data(employee_name, today)
        filtered = _filter_data(all_data, asset_type, status)

        if query_type == "list":
            text = _build_list(filtered, employee_name, asset_type, status, limit, today)
        elif query_type == "renewal":
            text = _build_renewal(filtered, employee_name, today)
        else:
            text = _build_summary(filtered, employee_name, asset_type, status, today)
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
