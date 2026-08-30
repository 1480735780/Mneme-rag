# -*- coding: utf-8 -*-
"""
agent.tool_catalog - 主 Agent 工具目录（对应 Java AgentToolCatalog）

固定注册 search_knowledge，并按意图树配置挂载当前可用的 MCP 工具：
    - resolve()：注册表与提示词解析一次并定格 → ResolvedCatalog（指纹 + 展示名在构造时算好）；
      同一次请求的指纹与 Toolkit 都从这份快照派生，重建之前所有请求看到的都是同一份。
    - build_toolkit(catalog)：按快照构建全新 agentscope Toolkit，过程中不再回读注册表，
      结果与快照指纹必然一致。
    - 指纹驱动懒重建（P1-4 provider 消费）：人设或工具目录变化 → 指纹不等 → 重建 Agent。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.tool.AgentToolCatalog
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List

from agentscope.tool import Toolkit

from agent.tools.knowledge_tool import DISPLAY_NAME, TOOL_NAME, KnowledgeSearchTool
from agent.tools.mcp_bridge import McpToolBridge
from rag.intent import IntentNode
from rag.mcp.executor import McpToolExecutor
from rag.mcp.model import McpToolDefinition
from rag.mcp.registry import McpToolRegistry
from rag.prompt.builder import AgentPromptResolver, AgentPromptSlot
from rag.service.knowledge_facade import KnowledgeSearchFacade

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpToolFingerprint:
    """单个 MCP 工具的指纹分量（参与懒重建比较）"""

    tool_id: str
    display_name: str
    description: str
    definition: McpToolDefinition


@dataclass(frozen=True)
class ToolCatalogFingerprint:
    """工具目录指纹：知识库工具声明 + 全部 MCP 工具分量（frozen dataclass 结构相等语义）"""

    knowledge_tool_description: str
    mcp_tools: List[McpToolFingerprint] = field(default_factory=list)


@dataclass(frozen=True)
class McpToolBinding:
    """一次解析定格的 MCP 工具绑定（意图树节点 × 注册表执行器）"""

    tool_id: str
    display_name: str
    description: str
    executor: McpToolExecutor


class ResolvedCatalog:
    """
    一次解析定格的工具目录：指纹与展示名在构造时算好

    快照随 Agent 实例一起缓存（P1-4 provider），重建之前所有请求看到的都是同一份。
    """

    def __init__(
        self,
        knowledge_tool_description: str,
        bindings: List[McpToolBinding],
        unavailable_tool_ids: List[str],
    ):
        self.knowledge_tool_description = knowledge_tool_description
        self.bindings: List[McpToolBinding] = list(bindings)
        self.unavailable_tool_ids: List[str] = list(unavailable_tool_ids)

        # 展示名表：SSE 工具进度事件用，未收录的工具回落原始名
        names: Dict[str, str] = {TOOL_NAME: DISPLAY_NAME}
        for binding in self.bindings:
            names[binding.tool_id] = binding.display_name
        self._display_names = names

        self.fingerprint = ToolCatalogFingerprint(
            knowledge_tool_description=knowledge_tool_description,
            mcp_tools=[
                McpToolFingerprint(
                    tool_id=b.tool_id,
                    display_name=b.display_name,
                    description=b.description,
                    definition=b.executor.get_tool_definition(),
                )
                for b in self.bindings
            ],
        )

    def display_name_of(self, tool_name: str) -> str:
        """SSE 工具进度事件的展示名，未收录的工具回落原始名"""
        return self._display_names.get(tool_name, tool_name)


class AgentToolCatalog:
    """
    主 Agent 工具目录（固定 search_knowledge + 意图树配置的 MCP 工具）

    Args:
        knowledge_search_facade: RAG-as-Tool 门面（P0 交付）
        intent_node_registry:    意图节点注册表（list_mcp_tool_nodes 提供配置的 MCP 工具节点）
        mcp_tool_registry:       MCP 工具注册表（当前可用的执行器）
        agent_prompt_resolver:   提示词解析器（KNOWLEDGE_TOOL_DESCRIPTION 槽位）
    """

    def __init__(
        self,
        knowledge_search_facade: KnowledgeSearchFacade,
        intent_node_registry,
        mcp_tool_registry: McpToolRegistry,
        agent_prompt_resolver: AgentPromptResolver,
    ):
        self._knowledge_search_facade = knowledge_search_facade
        self._intent_node_registry = intent_node_registry
        self._mcp_tool_registry = mcp_tool_registry
        self._agent_prompt_resolver = agent_prompt_resolver

    def resolve(self) -> ResolvedCatalog:
        """把注册表与提示词解析一次并定格（对应 Java resolve）"""
        unavailable_tool_ids: List[str] = []
        bindings = self._resolve_mcp_tool_bindings(unavailable_tool_ids)
        return ResolvedCatalog(self._resolve_knowledge_tool_description(), bindings, unavailable_tool_ids)

    async def build_toolkit(self, catalog: ResolvedCatalog) -> Toolkit:
        """按快照构建全新 agentscope Toolkit（add_tool 为异步；过程中不回读注册表，结果与指纹必然一致）"""
        toolkit = Toolkit()
        await toolkit.add_tool(
            KnowledgeSearchTool(catalog.knowledge_tool_description, self._knowledge_search_facade)
        )
        for binding in catalog.bindings:
            await toolkit.add_tool(McpToolBridge(binding.executor, binding.description))
        # 不可用只在重建这一刻报：解析每请求都走，放解析里会刷屏
        for tool_id in catalog.unavailable_tool_ids:
            logger.warning("意图树配置的 MCP 工具当前不可用, toolId: %s", tool_id)
        return toolkit

    def mcp_tool_count(self) -> int:
        """
        意图树已配置且 MCP 注册表当前可用的工具数（meta 探活据此报告 MCP 配置状态）

        不走整份解析：探活不该被知识库工具声明缺失连坐。
        """
        return len(self._resolve_mcp_tool_bindings([]))

    def _resolve_knowledge_tool_description(self) -> str:
        description = self._agent_prompt_resolver.resolve(AgentPromptSlot.KNOWLEDGE_TOOL_DESCRIPTION)
        if not description or not description.strip():
            raise ValueError("KNOWLEDGE_TOOL_DESCRIPTION 提示词不允许为空")
        return description

    def _resolve_mcp_tool_bindings(self, unavailable_tool_ids: List[str]) -> List[McpToolBinding]:
        """意图树配置与 MCP 注册表求交集；配了但当前没执行器的工具 ID 收进 unavailable_tool_ids"""
        nodes_by_tool_id: Dict[str, List[IntentNode]] = {}
        for node in self._intent_node_registry.list_mcp_tool_nodes():
            tool_id = (node.mcp_tool_id or "").strip()
            nodes_by_tool_id.setdefault(tool_id, []).append(node)

        executors = {ex.get_tool_id(): ex for ex in self._mcp_tool_registry.list_all_executors()}

        bindings: List[McpToolBinding] = []
        for tool_id, nodes in nodes_by_tool_id.items():
            executor = executors.get(tool_id)
            if executor is None:
                unavailable_tool_ids.append(tool_id)
                continue
            # 展示名取首个非空节点名；描述 = 各节点非空 description 去重逐行拼接
            display_name = next((n.name for n in nodes if n.name and n.name.strip()), tool_id)
            seen: List[str] = []
            for node in nodes:
                if node.description and node.description.strip() and node.description not in seen:
                    seen.append(node.description)
            bindings.append(McpToolBinding(tool_id, display_name, "\n".join(seen), executor))
        return bindings
