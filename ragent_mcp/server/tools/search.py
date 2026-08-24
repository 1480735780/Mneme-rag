# -*- coding: utf-8 -*-
"""
ragent_mcp.server.tools.search - youcom_search MCP 工具（对应 Java YouComSearchMcpExecutor）

独立部署边界（D7）：本模块不 import rag/app/core（You.com HTTP 逻辑与 bootstrap 侧
rag/websearch/client.py 有意重复，属服务级重复——独立 mcp-server 不依赖主应用）。

对齐 Java 语义：
    - You.com Search API（GET https://ydc-index.io/v1/search，X-API-Key 鉴权）
    - 仅当环境变量 YDC_API_KEY 存在时才注册工具（@ConditionalOnProperty 等价：
      「工具存在 ⟺ 可用」，缺 Key 注册只会诱导模型调用失败）
    - 参数校验：count 钳制（缺省 5、>20 → 20、<=0 → 5）；freshness 枚举 day/week/month/year
    - web+news 合并后统一截断到 count（count 表达「结果总条数上限」）
    - 摘录优先 description，缺失回退第一条 snippet
    - 非 200 报错不回显响应体（避免泄露账号信息）

可测试性：create_youcom_handler(api_key, api_url=None) 注入 api_url 指向本地 stub（对齐 Java
单测把 apiUrl 指到 stub 服务的做法）。
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional, Tuple

YOUCOM_TOOL_NAME = "youcom_search"
_ENV_API_KEY = "YDC_API_KEY"
_DEFAULT_URL = "https://ydc-index.io/v1/search"
_DEFAULT_COUNT = 5
_MAX_COUNT = 20
_FRESHNESS_VALUES = ("day", "week", "month", "year")


def is_youcom_enabled() -> bool:
    """工具是否可用（YDC_API_KEY 存在）；Key 缺失不注册（对齐 @ConditionalOnProperty）"""
    key = (os.environ.get(_ENV_API_KEY) or "").strip()
    return bool(key)


def build_youcom_tool_definition() -> Dict[str, Any]:
    return {
        "name": YOUCOM_TOOL_NAME,
        "description": "基于 You.com Search API 的联网搜索，返回带来源链接和摘录片段的网页与新闻结果。需要配置 YDC_API_KEY 环境变量",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或问题"},
                "count": {"type": "integer", "description": "最多返回的结果条数（网页+新闻合计），默认 5，最大 20", "default": 5},
                "freshness": {
                    "type": "string",
                    "description": "结果时效过滤：day(一天内)、week(一周内)、month(一月内)、year(一年内)，不传则不限",
                    "enum": list(_FRESHNESS_VALUES),
                },
            },
            "required": ["query"],
        },
    }


def _fetch_search(api_url: str, query: str, count: int, freshness: Optional[str], api_key: str) -> str:
    """调用 You.com Search API 并格式化结果（对齐 Java doSearch + formatResults）"""
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    url = f"{api_url}?query={urllib.parse.quote(query)}&count={count}"
    if freshness:
        url += f"&freshness={freshness}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _format_results(json.loads(resp.read().decode("utf-8")), count)
    except urllib.error.HTTPError as exc:
        # 不回显响应体，避免泄露账号信息；对齐 Java「非 200 抛异常状态码」（Java 侧 statusCode 分支）
        raise RuntimeError(f"You.com API 返回异常状态码: {exc.code}") from exc
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 网络/解析异常统一转可读错误
        raise RuntimeError(f"You.com API 调用失败: {exc}") from exc


def _format_results(root: Dict[str, Any], count: int) -> str:
    """响应 → 编号的 标题/链接/摘录 文本（web+news 合并截断到 count；对齐 Java formatResults）"""
    items: list = []
    results = root.get("results") or {}
    items.extend(results.get("web") or [])
    items.extend(results.get("news") or [])

    if not items:
        return "未检索到相关结果，请尝试更换关键词。"
    items = items[:count]

    lines = [f"检索完成，共 {len(items)} 条结果：", ""]
    for index, item in enumerate(items, 1):
        title = item.get("title") or "(无标题)"
        url = item.get("url") or ""
        excerpt = _resolve_excerpt(item)
        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   链接: {url}")
        if excerpt:
            lines.append(f"   摘录: {excerpt}")
        lines.append("")
    return "\n".join(lines).strip()


def _resolve_excerpt(item: Dict[str, Any]) -> str:
    """摘录优先 description，缺失回退第一条 snippet（对齐 Java resolveExcerpt）"""
    description = item.get("description") or ""
    if description:
        return description
    snippets = item.get("snippets") or []
    return snippets[0] if snippets else ""


def create_youcom_handler(api_key: str, api_url: Optional[str] = None) -> Callable[[Dict[str, Any]], Tuple[str, bool]]:
    """构造注入 api_url 的调用 handler（api_url 便于测试指向本地 stub；对齐 Java 可测试性设计）"""
    base_url = api_url or _DEFAULT_URL

    def handler(arguments: Dict[str, Any]) -> Tuple[str, bool]:
        query = arguments.get("query")
        if not query or not str(query).strip():
            return "请提供检索关键词 query", True
        query = str(query).strip()

        count = _int_or_default(arguments.get("count"), _DEFAULT_COUNT)
        if count <= 0:
            count = _DEFAULT_COUNT
        if count > _MAX_COUNT:
            count = _MAX_COUNT

        freshness = arguments.get("freshness")
        if freshness is not None and str(freshness).strip():
            freshness = str(freshness).strip()
            if freshness not in _FRESHNESS_VALUES:
                return f"freshness 参数不合法，可选值：{'、'.join(_FRESHNESS_VALUES)}", True
        else:
            freshness = None

        if not api_key.strip():
            return (
                "You.com 联网搜索未配置：请先设置环境变量 YDC_API_KEY"
                "（可在 https://you.com/platform/api-keys 获取），配置后重启 MCP Server 即可使用",
                True,
            )
        try:
            text = _fetch_search(base_url, query, count, freshness, api_key)
            return text, False
        except Exception as exc:  # noqa: BLE001 —— 对齐 Java catch(Exception) → errorResult
            return f"搜索失败: {exc}", True

    return handler


def handle_youcom_call(arguments: Dict[str, Any]) -> Tuple[str, bool]:
    """处理 youcom_search 调用 → (text, is_error)；Key 缺失 / 失败返回 isError"""
    handler = create_youcom_handler((os.environ.get(_ENV_API_KEY) or "").strip())
    return handler(arguments)


def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default
