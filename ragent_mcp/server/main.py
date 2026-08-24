# -*- coding: utf-8 -*-
"""
ragent_mcp.server.main - MCP Server 独立服务入口（对应 Java McpServerApplication + McpServerConfig）

FastMCP 由官方 mcp SDK 2.x 提供（本项目 ragent_mcp 是其适配层；包名不与 SDK 冲突，见包 README）。
    - 服务端：mcp.server.MCPServer（name="ragent-mcp-server"，version="0.0.1"）
    - 传输：Streamable HTTP（streamable_http_app，默认路径 /mcp，stateless_http=False 即有状态会话）
    - 协议版本：mcp 2.x 由客户端在 initialize 时协商（服务端取 params.protocolVersion，缺省 2025-03-26）；
      M1' 起客户端（官方 SDK / 自研 McpHttpClient）显式传 2025-06-18 对齐 Java SDK 有状态行为（D8/B9）
    - 启动：python -m ragent_mcp.server.main（uvicorn 挂 Starlette app，port 9099 对齐 Java）

独立部署边界（D7）：本模块（及 tools/）不 import rag/app/core，可独立进程部署。
工具：M1' 注册 weather_query；M2' 追加 sales_query/ticket_query/youcom_search（无 YDC_API_KEY 不注册）。
"""
from __future__ import annotations

import logging
from typing import Optional

from mcp.server import MCPServer

from ragent_mcp.server.tools.sales import SALES_TOOL_NAME, handle_sales_call
from ragent_mcp.server.tools.search import (
    YOUCOM_TOOL_NAME,
    handle_youcom_call,
    is_youcom_enabled,
)
from ragent_mcp.server.tools.ticket import TICKET_TOOL_NAME, handle_ticket_call
from ragent_mcp.server.tools.weather import (
    WEATHER_TOOL_NAME,
    handle_weather_call,
)

logger = logging.getLogger(__name__)

SERVER_NAME = "ragent-mcp-server"
SERVER_VERSION = "0.0.1"
HOST = "0.0.0.0"
PORT = 9099
MCP_PATH = "/mcp"

# 模块级 server 实例（工具注册集中在此；测试可导入复用）
server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION)


def _raise_if_error(text: str, is_error: bool) -> str:
    """is_error → 抛异常（MCP 转 CallToolResult.isError）"""
    if is_error:
        raise ValueError(text)
    return text


@server.tool(name=WEATHER_TOOL_NAME, description="查询城市天气信息，支持当前实时天气和未来多天预报")
def weather_query(city: str, queryType: str = "current", days: int = 3) -> str:
    """MCP 工具：weather_query（参数对齐 Java WeatherMcpExecutor.buildTool）"""
    return _raise_if_error(*handle_weather_call({"city": city, "queryType": queryType, "days": days}))


@server.tool(name=SALES_TOOL_NAME, description="查询软件销售数据，支持按地区、时间、产品、销售人员等维度筛选，支持汇总统计、排名、明细列表等多种查询")
def sales_query(
    region: Optional[str] = None,
    period: str = "本月",
    product: Optional[str] = None,
    salesPerson: Optional[str] = None,
    queryType: str = "summary",
    limit: int = 10,
) -> str:
    """MCP 工具：sales_query（参数对齐 Java SalesMcpExecutor.buildTool）"""
    return _raise_if_error(*handle_sales_call({
        "region": region, "period": period, "product": product,
        "salesPerson": salesPerson, "queryType": queryType, "limit": limit,
    }))


@server.tool(name=TICKET_TOOL_NAME, description="查询客户技术支持工单数据，支持按地区、状态、优先级、产品、客户等维度筛选，支持汇总概览、工单列表、统计分析等多种查询")
def ticket_query(
    region: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    product: Optional[str] = None,
    customerName: Optional[str] = None,
    queryType: str = "summary",
    limit: int = 10,
) -> str:
    """MCP 工具：ticket_query（参数对齐 Java TicketMcpExecutor.buildTool）"""
    return _raise_if_error(*handle_ticket_call({
        "region": region, "status": status, "priority": priority, "product": product,
        "customerName": customerName, "queryType": queryType, "limit": limit,
    }))


# youcom_search：仅 YDC_API_KEY 存在时注册（对齐 Java @ConditionalOnProperty，「工具存在 ⟺ 可用」）
if is_youcom_enabled():

    @server.tool(name=YOUCOM_TOOL_NAME, description="基于 You.com Search API 的联网搜索，返回带来源链接和摘录片段的网页与新闻结果。需要配置 YDC_API_KEY 环境变量")
    def youcom_search(query: str, count: int = 5, freshness: Optional[str] = None) -> str:
        """MCP 工具：youcom_search（参数对齐 Java YouComSearchMcpExecutor.buildTool）"""
        return _raise_if_error(*handle_youcom_call({"query": query, "count": count, "freshness": freshness}))
else:
    logger.info("YDC_API_KEY 未配置，youcom_search 工具不注册（可在配置后重启 MCP Server 启用）")


def streamable_app():
    """Starlette ASGI 应用（供 uvicorn 挂载；测试可复用）"""
    return server.streamable_http_app(streamable_http_path=MCP_PATH)


def run() -> None:
    """启动独立 MCP Server（port 9099，Streamable HTTP）"""
    import uvicorn

    logger.info("启动 ragent MCP Server: %s:%s%s", HOST, PORT, MCP_PATH)
    uvicorn.run(streamable_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    run()
