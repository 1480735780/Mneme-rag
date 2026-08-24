# ragent_mcp — MCP 协议层

基于 MCP（Model Context Protocol）的工具接入层：**服务端**向 Agent 暴露工具（weather/sales/ticket/联网搜索），**客户端**接入外部 MCP 服务。

> 包名说明：原 `mcp/`，P8 M1' 改名 `ragent_mcp/`——官方 Python SDK 包名是 `mcp`，本地占位包曾与之同名冲突（项目根目录在 sys.path 时 `import mcp` 命中本地包而非 SDK）。改名后官方 SDK 独占 `mcp` 名，本包以 ragent_mcp 命名（对齐 Java mcp-server 独立模块）。

## 功能说明

- **server/**：本项目的 MCP 服务端（FastMCP 独立进程，`python -m ragent_mcp.server.main`，port 9099）——暴露 weather_query / sales_query / ticket_query / youcom_search 四工具，供 Agent 或任意 MCP 客户端调用；
- **client.py**：MCP 客户端抽象——连接外部 MCP 服务、发现并调用其工具（`McpClient` 抽象 + `MemoryMcpClient` 内存占位；真实 HTTP 客户端 M5 交付）。

## 主要模块

| 目录/文件 | 说明 | 状态 |
|-----------|------|------|
| `client.py` | MCP 客户端：`McpClient` 抽象 + `MemoryMcpClient` 占位 + `McpHttpClient`（Streamable HTTP/JSON-RPC，长会话 2025-06-18） | ✅ M3' |
| `server/main.py` | MCPServer 服务端入口（客户端 initialize 协商协议版本；port 9099） | ✅ M1' |
| `server/tools/weather.py` | weather_query 工具（20 城坐标表 + 季节化天气 + Java hashCode seed） | ✅ M1' |
| `server/tools/sales.py` | sales_query 模拟数据工具（照抄 Java，四查询类型） | ✅ M2' |
| `server/tools/ticket.py` | ticket_query 模拟数据工具（照抄 Java，三查询类型） | ✅ M2' |
| `server/tools/search.py` | youcom_search 真实 HTTP 工具（YDC_API_KEY 缺失不注册） | ✅ M2' |

## 与其他模块的关系

```
agent/tools.py ──► ragent_mcp/server（本项目工具，进程内调用）
agent/tools.py ──► ragent_mcp/client.py（外部 MCP 服务）
rag/mcp/autoconfig ──► ragent_mcp/client（注册远程工具进工具注册表）
server/tools/search.py   ──► You.com HTTP（独立，D7 服务级重复）
server/tools/database.py ──► storage/database（数据访问）
```

- **依赖**：官方 `mcp` SDK（FastMCP / StreamableHttpClient）、`storage/`（数据库）、`common/`（异常/日志）；
- **被依赖**：`rag/mcp/`（客户端装配）、`agent/`（工具调用）、外部 MCP 客户端。

## 使用说明与注意事项

1. **协议版本**：FastMCP 与 McpHttpClient 两端显式 `protocol_version="2025-06-18"`（有状态会话），对齐 Java SDK 行为；
2. **工具声明**：每个工具的 name / description / inputSchema 需声明完整，便于 LLM 正确选择工具；
3. **隔离边界（D7）**：server/ 不 import rag/app/core（独立部署，`tests/test_mcp_import_boundary.py` 自动检查）；
4. **错误语义**：工具执行失败应返回结构化错误（isError），而非裸异常。
