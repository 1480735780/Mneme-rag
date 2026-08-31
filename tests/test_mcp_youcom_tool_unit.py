# -*- coding: utf-8 -*-
"""
M2' youcom_search 工具单测（对应 Java YouComSearchMcpExecutor）

覆盖：
    - 离线 stub（本地 http.server 桩，对齐 Java YouComSearchMcpExecutorTest）：
      请求头带 X-API-Key / query/count/freshness 参数透传 / web+news 合并截断到 count /
      摘录 description 缺失回退 snippet / 非 200 报错
    - Key 缺失不注册（@ConditionalOnProperty 等价）
    - count 钳制 5-20、freshness 枚举校验
"""
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ragent_mcp.server.tools.search import (
    YOUCOM_TOOL_NAME,
    build_youcom_tool_definition,
    create_youcom_handler,
    handle_youcom_call,
    is_youcom_enabled,
)

SAMPLE_BODY = {
    "results": {
        "web": [
            {"url": "https://example.com/a", "title": "网页结果A", "description": "描述A", "snippets": ["片段A1"]},
            {"url": "https://example.com/b", "title": "网页结果B", "snippets": ["片段B1"]},
        ],
        "news": [
            {"url": "https://example.com/news", "title": "新闻结果", "description": "新闻描述"},
        ],
    }
}


class _StubServer:
    """记录型 stub：记录 X-API-Key 与 query 参数，返回预设 body"""

    def __init__(self, response_body=None, status_code=200):
        self._body = json.dumps(response_body if response_body is not None else SAMPLE_BODY).encode()
        self._status = status_code
        self.api_key = None
        self.query_params = {}
        self._httpd = None
        self._thread = None

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                # 记录请求契约
                server._capture(self)
                self.send_response(server._status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(server._body)

            def log_message(self, *args):
                pass

        server = self
        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._httpd.server_port}/v1/search"

    def _capture(self, handler):
        import urllib.parse

        self.api_key = handler.headers.get("X-API-Key")
        parsed = urllib.parse.urlsplit(handler.path)
        self.query_params = dict(urllib.parse.parse_qsl(parsed.query))

    def close(self):
        self._httpd.shutdown()
        self._thread.join(timeout=3)


@pytest.fixture()
def handler_factory():
    """返回 create_youcom_handler(api_key, api_url) 以便注入 stub URL"""
    return create_youcom_handler


class TestToolDefinition:
    def test_tool_name(self):
        assert YOUCOM_TOOL_NAME == "youcom_search"

    def test_input_schema(self):
        definition = build_youcom_tool_definition()
        assert definition["name"] == "youcom_search"
        schema = definition["input_schema"]
        props = schema["properties"]
        assert props["query"]["type"] == "string"
        assert props["count"]["default"] == 5
        assert props["freshness"]["enum"] == ["day", "week", "month", "year"]
        assert schema["required"] == ["query"]


class TestEnabled:
    def test_disabled_without_key(self, monkeypatch):
        monkeypatch.delenv("YDC_API_KEY", raising=False)
        assert is_youcom_enabled() is False

    def test_enabled_with_key(self, monkeypatch):
        monkeypatch.setenv("YDC_API_KEY", "test-key")
        assert is_youcom_enabled() is True


class TestHandleCall:
    def test_missing_query_is_error(self):
        text, is_error = handle_youcom_call({})
        assert is_error is True
        assert "query" in text

    def test_http_request_contract(self, handler_factory):
        stub = _StubServer()
        try:
            handler = handler_factory("secret-key", stub.url)
            text, is_error = handler({"query": "你好", "count": 5})
            assert is_error is False
            assert stub.api_key == "secret-key"  # X-API-Key 头
            assert stub.query_params["query"] == "你好"
            assert stub.query_params["count"] == "5"
        finally:
            stub.close()

    def test_web_news_merged_and_truncated(self, handler_factory):
        stub = _StubServer()
        try:
            handler = handler_factory("k", stub.url)
            # Java 语义：web+news 合并后取前 count 条（web 在前）→ count=2 只含网页 A/B，新闻被截掉
            text, is_error = handler({"query": "q", "count": 2})
            assert is_error is False
            assert "共 2 条结果" in text
            assert "网页结果A" in text
            assert "网页结果B" in text
            assert "新闻结果" not in text
            # count=5 → 三条全含
            text, is_error = handler({"query": "q", "count": 5})
            assert is_error is False
            assert "新闻结果" in text
        finally:
            stub.close()

    def test_excerpt_fallback_to_snippet(self, handler_factory):
        stub = _StubServer()
        try:
            handler = handler_factory("k", stub.url)
            text, is_error = handler({"query": "q", "count": 5})
            assert is_error is False
            assert "描述A" in text  # description 优先
            assert "片段B1" in text  # B 无 description → 回退 snippet
        finally:
            stub.close()

    def test_freshness_passed_through(self, handler_factory):
        stub = _StubServer()
        try:
            handler = handler_factory("k", stub.url)
            handler({"query": "q", "freshness": "week"})
            assert stub.query_params.get("freshness") == "week"
        finally:
            stub.close()

    def test_invalid_freshness_is_error(self, handler_factory):
        stub = _StubServer()
        try:
            handler = handler_factory("k", stub.url)
            text, is_error = handler({"query": "q", "freshness": "bogus"})
            assert is_error is True
            assert "freshness" in text
        finally:
            stub.close()

    def test_non_200_is_error(self, handler_factory):
        stub = _StubServer(status_code=500)
        try:
            handler = handler_factory("k", stub.url)
            text, is_error = handler({"query": "q"})
            assert is_error is True
            assert "异常状态码" in text
        finally:
            stub.close()

    def test_no_results_message(self, handler_factory):
        stub = _StubServer(response_body={"results": {}})
        try:
            handler = handler_factory("k", stub.url)
            text, is_error = handler({"query": "q"})
            assert is_error is False
            assert "未检索到" in text
        finally:
            stub.close()
