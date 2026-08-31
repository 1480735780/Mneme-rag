# -*- coding: utf-8 -*-
"""
M1' 有状态握手测试（核心验证点 B9）：独立 mcp-server 的 Streamable HTTP 握手 + 官方客户端互操作

覆盖：
    - 手写 initialize（显式 protocolVersion=2025-06-18）→ 服务端协商为 2025-06-18 + 返回 Mcp-Session-Id
    - 官方 SDK Client 连上 → tools/list 含 weather_query、tools/call 正常返回天气
    - 服务端兼容官方客户端最新协议版本（协商非 2025-06-18 时也成功，证明多版本协商）

注：本测试起真实 uvicorn 线程（随机空闲端口），是轻量集成级回归；SDK 缺失时跳过。
"""
import json
import socket
import threading
import time
import urllib.request

import pytest

from ragent_mcp.server.main import streamable_app

MCP = pytest.importorskip("mcp")  # 未装官方 SDK 时跳过（不影响主应用测试）


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
    # 等待端口就绪
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


def _raw_initialize(url: str, protocol_version: str):
    """手写 JSON-RPC initialize（显式传 protocolVersion），返回 (http_status, result, session_id)"""
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.0.1"},
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode("utf-8")
        stripped = raw.strip()
        if stripped.startswith("{"):
            payload = json.loads(stripped)
        else:
            data_lines = [ln[6:] for ln in stripped.splitlines() if ln.startswith("data:")]
            payload = json.loads("".join(data_lines))
        session_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        result = payload.get("result") or payload.get("error")
        return resp.status, result, session_id


class TestStatefulHandshake:
    def test_negotiates_2025_06_18_and_returns_session(self, mcp_server_url):
        status, result, session_id = _raw_initialize(mcp_server_url, "2025-06-18")
        assert status == 200
        assert result.get("protocolVersion") == "2025-06-18"
        assert session_id  # 有状态模式核心：服务端返回 Mcp-Session-Id

    def test_supports_official_client_latest_version(self, mcp_server_url):
        # 官方客户端用 LATEST_HANDSHAKE_VERSION 协商，服务端须兼容（多版本协商）
        status, result, session_id = _raw_initialize(mcp_server_url, "2026-07-28")
        assert status == 200
        assert session_id


class TestOfficialClientInterop:
    def test_list_and_call_tools(self, mcp_server_url):
        import asyncio

        from mcp.client import Client

        async def run():
            async with Client(mcp_server_url) as client:
                listing = await client.list_tools()
                names = [t.name for t in listing.tools]
                assert "weather_query" in names
                # M2'：三常驻工具注册（无 YDC_API_KEY 时 youcom_search 不注册）
                assert "sales_query" in names
                assert "ticket_query" in names
                assert "youcom_search" not in names
                call = await client.call_tool("weather_query", {"city": "北京"})
                assert getattr(call, "content", None)
                text = call.content[0].text
                assert "【北京 今日天气】" in text
                assert "空气质量" in text
                # M2'：sales/ticket 调用可用
                sales = await client.call_tool("sales_query", {"period": "本月", "queryType": "summary"})
                assert "销售数据汇总" in sales.content[0].text
                ticket = await client.call_tool("ticket_query", {"queryType": "summary"})
                assert "工单汇总" in ticket.content[0].text

        asyncio.run(run())
