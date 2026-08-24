# -*- coding: utf-8 -*-
"""
agent - 独立 Agent 框架骨架（⛔ 显式放弃，P8 D3）

Java 侧无 agent 包（工具编排由 rag/core/mcp 在引擎内闭环完成）；Python 侧等价能力
已由以下落点承载，本骨架不再实现独立 Agent 框架：
    - 工具编排 / 参数提取 / 工具调用：rag/mcp/（注册表 / 执行器 / LLM 参数提取）
    - 多轮会话记忆：rag/memory/（store / service / summary）
    - 流水线骨架（agent_pipeline 占位）：core/pipeline/agent_pipeline.py（未实现，保留占位）
    - MCP 工具接入：ragent_mcp/（server 四工具 + client）

详见 docs/ragent-porting-gap-analysis.md §9 P8（agent/evaluation 处置登记）。
"""
