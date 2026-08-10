# mcp — MCP 工具层

基于 MCP（Model Context Protocol）的工具接入层：**服务端**向 Agent 暴露检索/数据库等工具，**客户端**接入外部 MCP 服务。

## 功能说明

- **server/**：本项目的 MCP 服务端——启动后提供标准 MCP 工具（检索、数据库查询），供 Agent 或其他 MCP 客户端调用；
- **client.py**：MCP 客户端——用于调用外部 MCP 服务的能力（如第三方工具服务器），复用进 agent/tools 体系。

## 主要模块

| 目录/文件 | 说明 | 状态 |
|-----------|------|------|
| `client.py` | MCP 客户端：连接外部 MCP 服务、发现并调用其工具 | 🚧 占位待实现 |
| `server/main.py` | MCP 服务端入口：协议初始化、工具注册、请求分发 | 🚧 占位待实现 |
| `server/tools/search.py` | 检索工具：包装 `rag/` 的检索能力（向量/混合检索）为 MCP 工具 | 🚧 占位待实现 |
| `server/tools/database.py` | 数据库工具：包装 `storage/database/` 的查询能力为 MCP 工具 | 🚧 占位待实现 |

> 🚧 = 文件结构已就绪，待编写实现

## 与其他模块的关系

```
agent/tools.py ──► mcp/server（本项目工具，进程内调用）
agent/tools.py ──► mcp/client.py（外部 MCP 服务）
server/tools/search.py   ──► rag/retrieval（检索实现）
server/tools/database.py ──► storage/database（数据访问）
```

- **依赖**：`rag/`（检索）、`storage/`（数据库）、`common/`（异常/日志）；
- **被依赖**：`agent/`（工具调用）、外部 MCP 客户端。

## 使用说明与注意事项

1. **协议版本**：实现前先确认目标 MCP 协议版本与 SDK 选型（官方 `mcp` Python SDK 或自研 JSON-RPC 通信）；
2. **工具声明**：每个工具的 name / description / inputSchema 需声明完整，便于 LLM 正确选择工具；
3. **鉴权与限流**：服务端工具若对外暴露，需接入 `common/security` 与 `common/middleware` 的鉴权/限流能力；
4. **错误语义**：工具执行失败应返回结构化错误（而非裸异常），与 `common/exception` 的错误分类对齐。
