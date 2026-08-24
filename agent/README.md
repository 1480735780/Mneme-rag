# agent — Agent 能力骨架

> ⛔ **P8 显式放弃（D3）**：本骨架不实现独立 Agent 框架。

**原因**：Java 侧无 `agent/` 包（工具编排由 `rag/core/mcp` 在引擎内闭环完成）；Python 侧等价能力已由既有模块承载，不为「移植」发明需求。

**等价能力落点**：
- **工具编排 / 参数提取 / 工具调用**：[rag/mcp/](../rag/mcp/)（McpToolRegistry / McpClientToolExecutor / LLM 参数提取，已交付）
- **多轮会话记忆**：[rag/memory/](../rag/memory/)（store / service / summary，已交付）
- **流水线骨架**：`core/pipeline/agent_pipeline.py`（占位，未实现）
- **MCP 工具接入**：[ragent_mcp/](../ragent_mcp/)（server 四工具 weather/sales/ticket/youcom_search + McpHttpClient）

**占位文件**：`executor.py` / `planner.py` / `memory.py` / `tools.py` 为空占位（无实现、无引用），保留以标记曾规划的骨架。

> 若后续出现真实的多轮自主 Agent 需求（规划→执行循环），应在 `core/pipeline/agent_pipeline.py` 之上另立项，复用 `rag/mcp` + `rag/memory` 能力。
