"""
MCP 客户端抽象 + MVP 内存占位实现 + 真实 Streamable HTTP 实现（协议层，对应 Java 官方 SDK 的 McpSyncClient）

协议层职责：连接远端 MCP Server、initialize / tools/list / tools/call / close，
并把原始 CallToolResult → McpToolResult 归一（编排层只透传）。

- MemoryMcpClient：MVP 内存占位（进程内注册工具与结果），让 McpClientAutoConfiguration 与检索
  接线无外部服务时跑通全链路；
- McpHttpClient（M3'）：真实 Streamable HTTP / JSON-RPC 客户端——长会话（initialize 一次 +
  Mcp-Session-Id 复用，不逐 call 重建，对齐 Java McpSyncClient）、协议版本由客户端显式指定
  （initialize 传 protocolVersion=2025-06-18，对齐 Java SDK 有状态行为，D8/B8）、JSON 与 SSE
  双形态响应解析、CallToolResult → McpToolResult 归一、HTTP 非 2xx / JSON-RPC error 抛异常、
  close 发 DELETE 终止会话。

对应 ragent 源码：
    - io.modelcontextprotocol.client.McpSyncClient（注入 McpClientToolExecutor / McpClientAutoConfiguration）
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from rag.mcp.model import McpTextContent, McpToolDefinition, McpToolResult

logger = logging.getLogger(__name__)

# 协议版本（有状态会话分水岭版本；由客户端在 initialize 显式指定，服务端协商）
PROTOCOL_VERSION = "2025-06-18"

_ACCEPT_HEADERS = "application/json, text/event-stream"


class McpClient(ABC):
    """
    MCP 客户端抽象（对应 Java McpSyncClient）

    实现须提供 initialize / list_tools / call_tool / close 四个生命周期操作。
    """

    @abstractmethod
    def initialize(self) -> None:
        """握手初始化（对应 Java initialize）；失败抛异常由装配层跳过该 server"""
        ...

    @abstractmethod
    def list_tools(self) -> List[McpToolDefinition]:
        """列出该 server 暴露的工具（对应 Java listTools）"""
        ...

    @abstractmethod
    def call_tool(self, name: str, arguments: Dict[str, object]) -> McpToolResult:
        """调用工具（对应 Java callTool）；返回已归一的 McpToolResult"""
        ...

    @abstractmethod
    def close(self) -> None:
        """关闭客户端、释放资源（对应 Java close）"""
        ...


class MemoryMcpClient(McpClient):
    """
    MVP 内存占位客户端：进程内注册工具与结果，不接真实 MCP Server

    Args:
        tools:   该 server 暴露的工具定义列表（默认空）
        results: 工具名 → 预设调用结果（默认空；未注册工具调用返回错误结果）
    """

    def __init__(
        self,
        tools: Optional[List[McpToolDefinition]] = None,
        results: Optional[Dict[str, McpToolResult]] = None,
    ):
        self._tools = list(tools or [])
        self._results = dict(results or {})
        self.connected = False

    def initialize(self) -> None:
        self.connected = True

    def list_tools(self) -> List[McpToolDefinition]:
        return list(self._tools)

    def call_tool(self, name: str, arguments: Dict[str, object]) -> McpToolResult:
        if name in self._results:
            return self._results[name]
        return McpToolResult.error(f"工具未注册: {name}")

    def close(self) -> None:
        self.connected = False


class McpHttpClient(McpClient):
    """
    真实 Streamable HTTP / JSON-RPC 客户端（对应 Java 官方 McpSyncClient + StreamableHTTPTransport）

    长会话模型（D8/B8）：initialize 仅一次，保存服务端返回的 Mcp-Session-Id，后续
    tools/list / tools/call 请求携带该头复用，不逐 call 重建（避免每次调用多一轮握手往返）。

    Args:
        url:      MCP Server 端点（如 http://127.0.0.1:9099/mcp）
        http:     可选注入 httpx.Client（测试可注入 stub；缺省自建）
        protocol_version: initialize 显式声明的协议版本（缺省 2025-06-18）
    """

    def __init__(self, url: str, http=None, protocol_version: str = PROTOCOL_VERSION):
        self._url = url
        if http is None:
            import httpx

            http = httpx.Client(timeout=10.0)
            self._owns_http = True
        else:
            self._owns_http = False
        self._http = http
        self.protocol_version = protocol_version
        self.session_id: Optional[str] = None
        self.negotiated_version: Optional[str] = None
        self.connected = False
        self._id = 0

    # ==================== 生命周期 ====================

    def initialize(self) -> None:
        """握手：POST initialize（显式 protocolVersion）→ 捕获 Mcp-Session-Id → 发 initialized 通知"""
        result = self._request("initialize", {
            "protocolVersion": self.protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "ragent-mcp-client", "version": "0.0.1"},
        }, expect_result=True)
        self.negotiated_version = result.get("protocolVersion")
        # 通知：notifications/initialized（无响应体）
        self._post_notification("notifications/initialized")
        self.connected = True

    def list_tools(self) -> List[McpToolDefinition]:
        result = self._request("tools/list", None, expect_result=True)
        definitions: List[McpToolDefinition] = []
        for item in result.get("tools") or []:
            definitions.append(McpToolDefinition(
                name=item.get("name", ""),
                description=item.get("description") or "",
                input_schema=item.get("inputSchema"),
            ))
        return definitions

    def call_tool(self, name: str, arguments: Dict[str, object]) -> McpToolResult:
        result = self._request("tools/call", {"name": name, "arguments": arguments}, expect_result=True)
        # CallToolResult → McpToolResult 归一（content 取 text 段；isError）
        contents = []
        for content in result.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "text":
                contents.append(McpTextContent(text=content.get("text", "")))
        return McpToolResult(
            content=contents,
            is_error=bool(result.get("isError")),
            structured_content=result.get("structuredContent"),
        )

    def close(self) -> None:
        """关闭：DELETE 终止会话（Streamable HTTP 会话清理）"""
        try:
            if self.session_id:
                self._http.delete(self._url, headers={"Mcp-Session-Id": self.session_id})
        except Exception:  # noqa: BLE001 —— 关闭失败不阻断
            logger.warning("MCP 客户端关闭失败（DELETE 终止会话）", exc_info=True)
        if self._owns_http:
            self._http.close()
        self.connected = False

    # ==================== 协议原语 ====================

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": _ACCEPT_HEADERS,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post_notification(self, method: str) -> None:
        """发送无 id 的通知（如 notifications/initialized）"""
        payload = json.dumps({"jsonrpc": "2.0", "method": method})
        self._http.post(self._url, content=payload, headers=self._headers())

    def _request(self, method: str, params: Optional[dict], expect_result: bool) -> dict:
        """发送 JSON-RPC 请求并解析响应（JSON 或 SSE 双形态）；非 2xx / JSON-RPC error 抛异常"""
        body: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            body["params"] = params
        response = self._http.post(
            self._url,
            content=json.dumps(body),
            headers=self._headers(),
        )
        if response.status_code >= 300:
            raise RuntimeError(f"MCP HTTP 请求失败: {response.status_code} {method}")
        # 捕获会话 id（initialize 响应头）
        session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id

        payload = _parse_response_body(response)
        if "error" in payload:
            err = payload["error"]
            raise RuntimeError(f"MCP JSON-RPC 错误: {err.get('code')} {err.get('message')}")
        if expect_result:
            return payload.get("result") or {}
        return payload


def _parse_response_body(response) -> dict:
    """解析响应体：JSON 或 SSE（text/event-stream，取 data: 行拼接）"""
    content_type = (response.headers.get("content-type") or "")
    raw = response.text
    if "text/event-stream" not in content_type:
        return json.loads(raw) if raw.strip() else {}
    data_lines = [ln[5:] for ln in raw.splitlines() if ln.startswith("data:")]
    return json.loads("".join(data_lines)) if data_lines else {}

