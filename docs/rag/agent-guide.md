# Agent MVP 对接说明（P1）

> 补齐对比文档 §12 P1「补 Agent MVP」——用现有 `RoutingLLMService`、`MultiChannelRetrievalEngine`、
> MCP registry 打通 **plan-execute-observe-answer** 最小闭环，经 `POST /agent/chat` JSON 端点对外暴露。

## 端点

```
POST /agent/chat
Content-Type: application/json

{
  "question": "北京明天天气如何？",
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]   # 可选
}
```

返回统一 Result 包装 + camelCase 结构：

| 字段 | 语义 |
|---|---|
| `answer` | 最终回答 |
| `steps` | 工具调用记录（`tool` / `params` / `observation` / `ok`），无调用为空列表 |
| `iterations` | 实际循环轮数（LLM 决策次数） |
| `error` | LLM 调用失败时的错误信息；正常为 `null` |

挂载条件：`agent_service` 装配（引擎 + LLM 就绪）才暴露端点，否则 404（半装配防护，同 eval 端点）。

## 输出协议（LLM 决策）

LLM 每轮必须输出**合法 JSON**（无多余文字）：

- 需要调用工具：`{"tool": "<工具名>", "params": {...}}`
- 已有足够信息回答：`{"answer": "<最终回答>"}`

容错：解析失败 / 非 JSON / 无 `tool` 与 `answer` 字段 → **原文本视为最终答案**。

## 工具源

| 工具 | 来源 | 说明 |
|---|---|---|
| MCP 工具 | `McpToolRegistry`（`DefaultMcpToolRegistry` + `McpClientAutoConfiguration`） | weather / sales / ticket / search 等；同步 `execute` 经 `asyncio.to_thread` 适配 |
| `knowledge_search` | 内置，注入 `MultiChannelRetrievalEngine` | 检索知识库，参数 `question`；返回片段按 `[1]` 编号，`context_top_k` 截断 |

接线时 MCP registry 注入槽优先；未注入则 `McpClientAutoConfiguration` 自动装配
（无配置 servers → 空注册表，仅保留内置 `knowledge_search`）。

## 终止语义

- `max_iterations` 上限（默认 5），达到仍未收敛 → `answer` 为「已达最大迭代次数」提示；
- LLM 调用失败 → 立即返回（`error` 携带异常信息）；
- 未知工具 / 工具异常 **不中断**循环（记 `ok=False` + observation 继续）。

## 实现

- 管线：[core/pipeline/agent_pipeline.py](../core/pipeline/agent_pipeline.py) `AgentPipeline`（ReAct 闭环 + `parse_decision` + `AgentTool`/`AgentStep`/`AgentResult`）
- 门面：[rag/service/agent_service.py](../rag/service/agent_service.py) `AgentChatService`（`AgentResult` → snake_case dict；history 入参转 `[Message]`）
- 端点：[rag/controller/agent_controller.py](../rag/controller/agent_controller.py)（`POST /agent/chat`，camelize 边界转换）
- 装配：[app/wiring.py](../app/wiring.py) `_wire_agent_services`（从引擎提取组件 + MCP 自动装配；引擎/LLM 未就绪 → `agent_service=None`）
- 挂载：[app/factory.py](../app/factory.py)（`agent_service` 就绪才 include_router）
