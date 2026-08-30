# -*- coding: utf-8 -*-
"""
agent - Agent 执行架构域（v1.1 复活，对齐 ragent-new agent 包）

> **历史处置（已作废）**：P8 曾以「Java 侧（ragent-study）无 agent 包」为由显式放弃本骨架
> （D3，原始登记见 docs/ragent-file-by-file-comparison.md §11）。v1.1 对齐目标切换为
> ragent-new（v2 ReAct 架构，47 文件的 agent 模块）后，该处置**作废**——依据
> docs/v1.1-agent-alignment-gap-report.md（§2 核心缺口 + §8 P1 计划）重建本域。

P1 包结构（对齐 ragent-new agent 模块，逐包落地、每包同轮销案）：

    agent/config.py        引擎条件装配 + Agent 参数（对应 AgentProperties / ConditionalOnAgentEngine）
    agent/models.py        DTO 与枚举（AgentBlock 轨迹块 + SSE 五类载荷 + 消息状态/事件类型枚举）
    agent/dao.py           t_agent_conversation / t_agent_message 数据访问（P0 已建表；dao 层保证唯一性）
    agent/tool_catalog.py  工具目录 + 指纹快照（对应 AgentToolCatalog.ResolvedCatalog）
    agent/tools/           knowledge_tool（包 KnowledgeSearchFacade）+ mcp_bridge（对应 KnowledgeSearchTool/McpToolBridge）
    agent/memory/          ContextTrimmer + Compaction（对应 AgentContextTrimmer / AgentContextCompactionMiddleware）
    agent/state_store.py   t_agent_state 状态存取（对应 PgAgentStateStore，PG JSONB + InMemory 兜底）
    agent/provider.py      ReAct Agent 单例供给（对应 ReActAgentProvider；内核 = agentscope Python，决策 1A）
    agent/service.py 等    SSE 流式服务层（对应 AgentChatServiceImpl / AgentStreamEventBridge / AgentRunGate）

配套决策（2026-08-29，详见 v1.1 报告 §9 进度节）：
    1A 引擎内核 = agentscope Python（钉 >=2.0,<2.1）；2A 模型 = OpenAIChatModel 直连 ai.yaml provider；
    3B RAG_ENGINE_TYPE 默认 workflow（P2 切 agent）；SSE 走 SseQueue 硬约束；mcp 钉版放宽 >=1.29,<2.0。

旧 P8 空占位文件 planner/executor/memory/tools.py（0 字节、无引用）已随本声明作废一并删除。
"""
