# agent — Agent 执行架构域（v1.1）

> ♻️ **v1.1 复活（2026-08-29）**：P8 的「显式放弃（D3）」处置**作废**。原因：v1.1 对齐目标从
> ragent-study（v1，无 agent 包）切换为 **ragent-new（v2 ReAct 架构，47 文件的 agent 模块）**，
> Agent 引擎成为 v1.1 的核心缺口。原始放弃登记与作废标注见
> [docs/ragent-file-by-file-comparison.md](../docs/ragent-file-by-file-comparison.md) §11。

## 对齐目标

ragent-new 的 `agent/` 模块（构建在 AgentScope Java 2.0.2 上）：ReAct 循环、事件流、状态存储、
middleware 由框架提供；ragent-new 自身负责装配（ReActAgentProvider）、工具目录（AgentToolCatalog）、
记忆裁剪（ContextTrimmer/Compaction）、状态持久化（PgAgentStateStore）与 SSE 服务层。

Python 侧承接：**内核 = agentscope Python**（决策 1A，钉 `agentscope>=2.0,<2.1`，API 与 Java 版同源），
ragent-new 的装配/目录/裁剪/持久化/服务层逐文件移植（决策 2A 模型直连）。
**决策 3B 已落地（2026-08-30）**：`RAG_ENGINE_TYPE` 默认 agent（P2 端点 + 前端 Agent Chat
交付并经 ollama qwen2.5:3b 真模型实测后切换）；退回 v1 编排管线显式设 `RAG_ENGINE_TYPE=workflow`。

## 包结构（P1 逐包落地）

| 模块 | 对应 ragent-new | 说明 |
|---|---|---|
| `config.py` | AgentProperties / ConditionalOnAgentEngine | 引擎条件装配（`RAG_ENGINE_TYPE`）+ 参数 |
| `models.py` | dto/* + enums/* | AgentBlock 轨迹块 + SSE 五类载荷 + 枚举 |
| `dao.py` | dao/mapper/* | t_agent_conversation / t_agent_message（P0 已建表） |
| `tool_catalog.py` | AgentToolCatalog | 工具目录 + 指纹快照（懒重建判据） |
| `tools/` | KnowledgeSearchTool / McpToolBridge | knowledge_search（包 KnowledgeSearchFacade）+ MCP 桥 |
| `memory/` | AgentContextTrimmer / CompactionMiddleware | 工具结果等长替换 + 推理前裁剪 |
| `state_store.py` | PgAgentStateStore | t_agent_state（PG JSONB + InMemory 兜底） |
| `provider.py` | ReActAgentProvider | 单例复用 + 人设/目录指纹懒重建 |
| `service.py` 等 | AgentChatServiceImpl / StreamEventBridge / RunGate | SSE 流式编排 + 并发闸门 + 取消接线 |
| `controller.py`（P2） | AgentChatController / AgentConversationController / AgentMetaController | `/agent/v1/chat`（SSE）+ stop + 会话 CRUD + meta，factory 按 `agent_engine_chat_service` 条件挂载 |

完整规格与进度销案：[docs/v1.1-agent-alignment-gap-report.md](../docs/v1.1-agent-alignment-gap-report.md)（§2 逐文件对照、§8 优先级表、§9 进度节）。

## 历史处置（保留备查）

P8（2026-08-25 前后）曾登记：`agent/` 骨架显式放弃（D3）——彼时对齐 ragent-study，Java 侧无 agent 包，
等价能力由 `rag/mcp/` + `rag/memory/` + `core/pipeline/agent_pipeline.py`（MVP，plan-execute-observe 循环）
承载，该结论在 workflow 模式下依然成立；空占位文件 planner/executor/memory/tools.py 已删除。
