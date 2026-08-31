# -*- coding: utf-8 -*-
"""
M3' McpHttpClient 协议单测（本地 stub 模拟 MCP Streamable HTTP server）

覆盖（对齐 D8 长会话 + 2025-06-18）：
    - initialize：请求体含 protocolVersion=2025-06-18 / clientInfo / capabilities；
      响应头 Mcp-Session-Id 被捕获保存
    - 长会话：list_tools / call_tool 请求携带 Mcp-Session-Id 头（stub 记录断言，非每 call 重建）
    - notifications/initialized 通知在握手后发送
    - list_tools：解析 result.tools → McpToolDefinition（name/description/inputSchema）
    - call_tool：tools/call 请求体 {name, arguments}；CallToolResult → McpToolResult（text 拼接 + isError）
    - JSON 与 SSE 双形态响应
    - JSON-RPC error / HTTP 非 2xx → 抛异常
    - close：DELETE 终止会话
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rag.mcp.model import McpToolDefinition, McpToolResult
from ragent_mcp.client import McpHttpClient

# 模拟 MCP server 的工具清单
FAKE_TOOLS = [
    {"name": "weather_query", "description": "查天气", "inputSchema": {"type": "object", "required": ["city"]}},
    {"name": "sales_query", "description": "查销售", "inputSchema": {"type": "object"}},
]


class _FakeMcpServer:
    """记录型 stub：状态机（initialize → session → tools/list → tools/call），响应可配 JSON/SSE"""

    def __init__(self, response_mode: str = "json"):
        self.response_mode = response_mode
        self.requests: list = []  # (path, headers, body_dict)
        self.session_id = "fake-session-123"
        self._httpd = None
        self._thread = None

        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw.decode("utf-8")) if raw else {}
                server.requests.append((self.path, dict(self.headers), body))
                method = body.get("method")

                if method == "initialize":
                    result = {
                        "protocolVersion": body.get("params", {}).get("protocolVersion"),
                        "capabilities": {},
                        "serverInfo": {"name": "fake", "version": "0.0.1"},
                    }
                    server._respond(self, result, session=True)
                elif method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                elif method == "tools/list":
                    server._respond(self, {"tools": FAKE_TOOLS})
                elif method == "tools/call":
                    result = {
                        "content": [{"type": "text", "text": f"结果:{body['params']['name']}"}],
                        "isError": False,
                    }
                    server._respond(self, result)
                else:
                    server._respond(self, {"error": {"code": -32601, "message": "method not found"}}, status=400)

            def do_DELETE(self):
                server.requests.append((self.path, dict(self.headers), None))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._httpd.server_port}/mcp"

    def _respond(self, handler, result, session=False, status=200):
        payload = {"jsonrpc": "2.0", "id": 1, "result": result}
        body = json.dumps(payload)
        handler.send_response(status)
        if session:
            handler.send_header("Mcp-Session-Id", self.session_id)
        if self.response_mode == "sse":
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(b"event: message\ndata: ")
            handler.wfile.write(body.encode())
            handler.wfile.write(b"\n\n")
        else:
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(body.encode())

    def close(self):
        self._httpd.shutdown()
        self._thread.join(timeout=3)

    def bodies(self):
        return [r[2] for r in self.requests]


@pytest.fixture(params=["json", "sse"])
def server(request):
    stub = _FakeMcpServer(response_mode=request.param)
    yield stub
    stub.close()


class TestInitialize:
    def test_initialize_handshake(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        # 握手请求体：protocolVersion=2025-06-18 + clientInfo + capabilities
        init_body = server.bodies()[0]
        assert init_body["method"] == "initialize"
        assert init_body["params"]["protocolVersion"] == "2025-06-18"
        assert init_body["params"]["clientInfo"]["name"]
        assert "capabilities" in init_body["params"]
        # 会话 id 被捕获
        assert client.session_id == server.session_id
        # notifications/initialized 已发送
        assert any(b and b.get("method") == "notifications/initialized" for b in server.bodies())

    def test_initialize_negotiates_version(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        assert client.negotiated_version == "2025-06-18"


class TestLongSession:
    def test_session_header_reused_across_calls(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        client.list_tools()
        client.call_tool("weather_query", {"city": "北京"})
        # list/call 请求都携带 Mcp-Session-Id 头（长会话，非每 call 重建）
        session_headers = [
            r[1].get("Mcp-Session-Id")
            for r in server.requests
            if r[2] and r[2].get("method") in ("tools/list", "tools/call")
        ]
        assert session_headers == [server.session_id, server.session_id]


class TestListTools:
    def test_parses_definitions(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        tools = client.list_tools()
        assert len(tools) == 2
        assert all(isinstance(t, McpToolDefinition) for t in tools)
        assert tools[0].name == "weather_query"
        assert tools[0].description == "查天气"
        assert tools[0].input_schema["required"] == ["city"]

    def test_request_method(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        client.list_tools()
        last = server.bodies()[-1]
        assert last["method"] == "tools/list"


class TestCallTool:
    def test_request_body(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        client.call_tool("weather_query", {"city": "北京"})
        call = server.bodies()[-1]
        assert call["method"] == "tools/call"
        assert call["params"]["name"] == "weather_query"
        assert call["params"]["arguments"] == {"city": "北京"}

    def test_result_normalized(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        result = client.call_tool("weather_query", {"city": "北京"})
        assert isinstance(result, McpToolResult)
        assert result.is_error is False
        assert result.to_text() == "结果:weather_query"


class TestErrors:
    def test_http_non_2xx_raises(self):
        import http.server as hs

        class FailHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")

            def log_message(self, *args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), FailHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{httpd.server_port}/mcp"
            client = McpHttpClient(url)
            with pytest.raises(RuntimeError):
                client.initialize()
        finally:
            httpd.shutdown()

    def test_close_sends_delete(self, server):
        client = McpHttpClient(server.url)
        client.initialize()
        client.close()
        assert any(r[0] == "/mcp" and r[2] is None for r in server.requests)  # DELETE 请求
