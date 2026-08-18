"""
MCP 工具注册表（对应 Java McpToolRegistry + DefaultMcpToolRegistry）

接口：register / unregister / get_executor / list_all_tools / list_all_executors /
      contains / size。

默认实现：进程内 dict（toolId → executor）；构造时自动注册注入的 executor 列表
（对齐 Java @PostConstruct init() 的「Spring 容器内自动发现注册」——Python 无容器，
改为构造注入已发现的执行器）；重复 toolId 覆盖并告警。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.McpToolRegistry
    - com.nageoffer.ai.ragent.rag.core.mcp.DefaultMcpToolRegistry
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from rag.mcp.executor import McpToolExecutor
from rag.mcp.model import McpToolDefinition

logger = logging.getLogger(__name__)


class McpToolRegistry(ABC):
    """MCP 工具注册表接口（对应 Java McpToolRegistry）"""

    @abstractmethod
    def register(self, executor: McpToolExecutor) -> None:
        """注册工具执行器（重复 toolId 覆盖）"""
        ...

    @abstractmethod
    def unregister(self, tool_id: str) -> None:
        """注销工具"""
        ...

    @abstractmethod
    def get_executor(self, tool_id: str) -> Optional[McpToolExecutor]:
        """按工具 ID 获取执行器（不存在返回 None，对应 Java Optional）"""
        ...

    @abstractmethod
    def list_all_tools(self) -> List[McpToolDefinition]:
        """获取所有已注册的工具定义"""
        ...

    @abstractmethod
    def list_all_executors(self) -> List[McpToolExecutor]:
        """获取所有已注册的工具执行器"""
        ...

    @abstractmethod
    def contains(self, tool_id: str) -> bool:
        """检查工具是否已注册"""
        ...

    @abstractmethod
    def size(self) -> int:
        """已注册工具数量"""
        ...


class DefaultMcpToolRegistry(McpToolRegistry):
    """
    内存版注册表（对应 Java DefaultMcpToolRegistry）

    Args:
        auto_discovered_executors: 自动注册的执行器列表（对齐 Java 容器内发现；可省略）
    """

    def __init__(
        self,
        auto_discovered_executors: Optional[List[McpToolExecutor]] = None,
    ):
        self._executors: Dict[str, McpToolExecutor] = {}
        discovered = auto_discovered_executors or []
        if not discovered:
            logger.info("MCP 工具注册跳过, 未发现任何工具执行器")
        for executor in discovered:
            self.register(executor)
        if discovered:
            logger.info("MCP 工具自动注册完成, 共注册 %d 个工具", len(discovered))

    def register(self, executor: McpToolExecutor) -> None:
        if executor is None or executor.get_tool_definition() is None:
            logger.warning("尝试注册空的执行器，已忽略")
            return
        tool_id = executor.get_tool_id()
        if not tool_id or not tool_id.strip():
            logger.warning("工具 ID 为空，已忽略")
            return
        existing = self._executors.get(tool_id)
        self._executors[tool_id] = executor
        if existing is not None:
            logger.warning("工具 %s 已存在，已覆盖", tool_id)
        else:
            logger.info("MCP 工具注册成功, toolId: %s", tool_id)

    def unregister(self, tool_id: str) -> None:
        removed = self._executors.pop(tool_id, None)
        if removed is not None:
            logger.info("MCP 工具注销成功, toolId: %s", tool_id)

    def get_executor(self, tool_id: str) -> Optional[McpToolExecutor]:
        return self._executors.get(tool_id)

    def list_all_tools(self) -> List[McpToolDefinition]:
        return [executor.get_tool_definition() for executor in self._executors.values()]

    def list_all_executors(self) -> List[McpToolExecutor]:
        return list(self._executors.values())

    def contains(self, tool_id: str) -> bool:
        return tool_id in self._executors

    def size(self) -> int:
        return len(self._executors)
