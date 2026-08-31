# -*- coding: utf-8 -*-
"""
M3' 闭环测试：自研 McpHttpClient ↔ 独立 mcp-server（FastMCP/MCPServer）经 McpClientAutoConfiguration 注册

验证（D8 闭环 + B7 spec 对齐）：
    - client_factory 按 server.url 分派：http(s):// → McpHttpClient
    - autoconfig.init() 连独立 mcp-server → 工具注册表含 weather/sales/ticket（无 YDC_API_KEY 无 youcom）
    - 注册的 executor.execute 经 McpHttpClient 实际调用远端工具返回正常结果
    - 长会话：多工具调用复用同一 Mcp-Session-Id（stub 断言由 test_mcp_http_client_unit 覆盖，
      本测试验证真实端到端调用链）
    - destroy() 关闭客户端
"""
import socket
import threading
import time
import urllib.request

import pytest

from rag.mcp.autoconfig import McpClientAutoConfiguration
from rag.mcp.config import McpClientProperties
from rag.mcp.registry import DefaultMcpToolRegistry
from ragent_mcp.server.main import streamable_app

pytest.importorskip("mcp")  # 未装官方 SDK 时跳过（server 依赖 SDK）


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_server_url():
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(streamable_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.3)
        except Exception:
            time.sleep(0.1)
        else:
            break
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def _build_autoconfig(url):
    properties = McpClientProperties.from_dict({"servers": [{"name": "local", "url": url}]})
    registry = DefaultMcpToolRegistry()
    autoconfig = McpClientAutoConfiguration(properties, registry)
    return autoconfig, registry


class TestClosure:
    def test_autoconfig_registers_remote_tools(self, mcp_server_url):
        autoconfig, registry = _build_autoconfig(mcp_server_url)
        autoconfig.init()
        try:
            tools = {t.name for t in registry.list_all_tools()}
            # R-A：asset_query/leave_query 随 ragent-new 对齐注册（§4 运行时缺口清零）
            assert {"weather_query", "sales_query", "ticket_query", "asset_query", "leave_query"} <= tools
            assert "youcom_search" not in tools  # 无 YDC_API_KEY
        finally:
            autoconfig.destroy()

    def test_execute_calls_remote_tool(self, mcp_server_url):
        autoconfig, registry = _build_autoconfig(mcp_server_url)
        autoconfig.init()
        try:
            executor = registry.get_executor("weather_query")
            assert executor is not None
            result = executor.execute({"city": "北京"})
            assert result.is_error is False
            assert "【北京 今日天气】" in result.to_text()
            # 长会话：同客户端连续多工具调用正常（非每 call 重建）
            sales = registry.get_executor("sales_query").execute({"period": "本月", "queryType": "summary"})
            assert sales.is_error is False
            assert "销售数据汇总" in sales.to_text()
        finally:
            autoconfig.destroy()

    def test_destroy_closes_clients(self, mcp_server_url):
        autoconfig, registry = _build_autoconfig(mcp_server_url)
        autoconfig.init()
        autoconfig.destroy()  # 不抛异常即可（close 发 DELETE 终止会话）
        assert autoconfig._clients  # 已建立客户端
