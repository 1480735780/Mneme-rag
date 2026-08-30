# -*- coding: utf-8 -*-
"""
core.pipeline.agent_pipeline - Agent MVP（plan-execute-observe-answer 最小闭环）

ReAct 风格智能体管线（对比文档 §12 P1 Agent MVP 落点）：
    系统提示（工具清单 + JSON 输出协议）→ LLM 决策 → 执行工具 → observation 回填 → 迭代 → 最终答案。

复用既有组件：
    - LLMService（生产为 RoutingLLMService）：决策与回答生成（同步 chat）
    - McpToolRegistry：外部 MCP 工具执行器（weather/sales/ticket/search 等）
    - MultiChannelRetrievalEngine：内置 knowledge_search 工具（知识库检索）

输出协议（LLM 必须输出合法 JSON，无多余文字）：
    - 调用工具：{"tool": "<工具名>", "params": {...}}
    - 直接回答：{"answer": "<最终回答>"}
容错：解析失败 / 非 JSON / 无 tool 与 answer 字段 → 原文本视为最终答案。

终止：max_iterations 上限（默认 5）；未知工具 / 工具异常不中断（记 observation 继续）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.llm.schema import ChatRequest, Message
from rag.intent import SubQuestionIntent
from rag.mcp import McpToolExecutor, McpToolRegistry
from rag.retrieval.schema import RetrievalBudget

logger = logging.getLogger(__name__)

# 本 run 的近期历史（工具层经 ContextVar 读取，Task 隔离；管线实例跨请求共享不串会话）
# 对齐 Java KnowledgeSearchTool：改写只用近期轮次消解指代，取 2 轮（4 条消息）
_REWRITE_CONTEXT_MESSAGES = 4
_recent_history: ContextVar[Optional[List[Message]]] = ContextVar(
    "agent_recent_history", default=None
)


@dataclass
class AgentTool:
    """Agent 内部工具抽象：name/description/input_schema + 异步 handler

    handler 签名：async (params: Dict[str, Any]) -> str（返回 observation 文本）。
    """

    name: str
    description: str
    handler: Callable[[Dict[str, Any]], Awaitable[str]]
    input_schema: Optional[Dict[str, Any]] = None


@dataclass
class AgentStep:
    """一次工具调用记录（供结果报告与 observation 回填）"""

    tool: str
    params: Dict[str, Any]
    observation: str
    ok: bool = True


@dataclass
class AgentResult:
    """一次 Agent 会话结果"""

    question: str
    answer: str
    steps: List[AgentStep] = field(default_factory=list)
    iterations: int = 0
    error: Optional[str] = None


def parse_decision(text: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 决策：合法 JSON 且含 tool → 工具调用 dict；含 answer → 结束 dict；否则 None"""
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "tool" in data:
        return {"tool": str(data["tool"]).strip(), "params": data.get("params") or {}}
    if "answer" in data:
        return {"answer": str(data["answer"])}
    return None


async def _call_mcp_executor(executor: McpToolExecutor, params: Dict[str, Any]) -> str:
    """同步 MCP executor 经 asyncio.to_thread 适配为异步 handler"""
    result = await asyncio.to_thread(executor.execute, params)
    if result is None:
        return "工具调用返回空结果"
    return result.to_text()


class AgentPipeline:
    """plan-execute-observe-answer 最小闭环

    Args:
        llm_service:         LLM 服务（同步 chat；生产注入 RoutingLLMService）
        tool_registry:       MCP 工具注册表（可选；提供外部工具）
        retrieval_engine:    MultiChannelRetrievalEngine（可选；注册内置 knowledge_search 工具）
        budget:              检索预算（knowledge_search 用；默认 RetrievalBudget()）
        scope_resolver:      检索作用域解析器（可选；缺省用引擎内部）
        context_top_k:       knowledge_search 返回的片段数上限（默认 5）
        max_iterations:      循环上限（默认 5）
        extra_tools:         测试/扩展注入的自定义 AgentTool 列表（可选）
        knowledge_facade:    知识检索门面（可选；注入后 knowledge_search 走完整 RAG 管线
                             ——改写/意图/歧义引导/检索/KB_ANSWER 合成，对应 Java KnowledgeSearchTool；
                             未注入时保留裸检索兜底）
    """

    def __init__(
        self,
        llm_service: Any,
        *,
        tool_registry: Optional[McpToolRegistry] = None,
        retrieval_engine: Any = None,
        budget: Optional[RetrievalBudget] = None,
        scope_resolver: Any = None,
        context_top_k: int = 5,
        max_iterations: int = 5,
        extra_tools: Optional[List[AgentTool]] = None,
        knowledge_facade: Any = None,
    ) -> None:
        self._llm_service = llm_service
        self._max_iterations = max_iterations
        self._context_top_k = context_top_k
        self._retrieval_engine = retrieval_engine
        self._budget = budget or RetrievalBudget()
        self._scope_resolver = scope_resolver
        self._knowledge_facade = knowledge_facade
        self._tools: Dict[str, AgentTool] = {}
        self._load_extra_tools(extra_tools)
        self._load_mcp_tools(tool_registry)
        self._load_knowledge_tool()

    # ==================== 工具装配 ====================

    def _load_extra_tools(self, extra_tools: Optional[List[AgentTool]]) -> None:
        for tool in extra_tools or []:
            if tool and tool.name:
                self._tools[tool.name] = tool

    def _load_mcp_tools(self, registry: Optional[McpToolRegistry]) -> None:
        if registry is None:
            return
        for executor in registry.list_all_executors():
            definition = executor.get_tool_definition()
            if definition is None or not (definition.name or "").strip():
                continue
            name = definition.name
            self._tools[name] = AgentTool(
                name=name,
                description=definition.description,
                input_schema=definition.input_schema,
                handler=lambda params, ex=executor: _call_mcp_executor(ex, params),
            )

    def _load_knowledge_tool(self) -> None:
        if self._retrieval_engine is None and self._knowledge_facade is None:
            return
        self._tools["knowledge_search"] = AgentTool(
            name="knowledge_search",
            description="检索知识库，获取与问题相关的文档片段。",
            input_schema={
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            handler=self._knowledge_search,
        )

    async def _knowledge_search(self, params: Dict[str, Any]) -> str:
        question = str(params.get("question") or params.get("query") or "").strip()
        if not question:
            return "knowledge_search 需要参数 question"
        if self._knowledge_facade is not None:
            # 完整管线：改写（带近期轮次做指代消解）→ 意图 → 歧义引导 → 检索 → KB_ANSWER 合成
            return await self._knowledge_facade.search(question, _recent_history.get())
        # 降级：未注入门面时保留裸检索兜底（MVP 兼容）
        sub_intent = SubQuestionIntent(sub_question=question)
        result = await self._retrieval_engine.retrieve_knowledge_channels(
            sub_intent, self._budget, self._scope_resolver
        )
        chunks = result.chunks[: self._context_top_k]
        if not chunks:
            return "未检索到相关知识。"
        return "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(chunks))

    # ==================== 提示词 ====================

    def _system_prompt(self) -> str:
        lines = []
        for name, tool in sorted(self._tools.items()):
            schema = json.dumps(tool.input_schema, ensure_ascii=False) if tool.input_schema else "{}"
            lines.append(f"- {name}: {tool.description}（参数 schema: {schema}）")
        tool_block = "\n".join(lines) if lines else "（无可用工具）"
        return (
            "你是智能体助手，需要基于工具返回的结果回答用户问题。可用工具：\n"
            f"{tool_block}\n\n"
            "输出必须是合法 JSON（不要包含多余文字）：\n"
            '- 需要调用工具时：{"tool": "工具名", "params": {...}}\n'
            '- 已有足够信息回答时：{"answer": "最终回答"}'
        )

    def _build_messages(self, question: str, history: Optional[List[Message]]) -> List[Message]:
        messages = [Message.system(self._system_prompt())]
        if history:
            messages.extend(history)
        messages.append(Message.user(question))
        return messages

    # ==================== 主循环 ====================

    async def run(
        self,
        question: str,
        history: Optional[List[Message]] = None,
        *,
        max_iterations: Optional[int] = None,
    ) -> AgentResult:
        limit = max_iterations if max_iterations is not None else self._max_iterations
        messages = self._build_messages(question, history)
        # 本 run 的近期历史挂 ContextVar：knowledge_search 走门面时仅用于改写阶段的指代消解
        history_token = _recent_history.set(list(history or [])[-_REWRITE_CONTEXT_MESSAGES:])
        steps: List[AgentStep] = []
        answer = ""
        iterations = 0
        try:
            return await self._run_loop(
                question, messages, history, limit, steps
            )
        finally:
            _recent_history.reset(history_token)

    async def _run_loop(
        self,
        question: str,
        messages: List[Message],
        history: Optional[List[Message]],
        limit: int,
        steps: List[AgentStep],
    ) -> AgentResult:
        answer = ""
        iterations = 0
        while iterations < limit:
            iterations += 1
            try:
                text = await self._llm_service.chat(
                    ChatRequest(messages=messages, temperature=0.3, thinking=False)
                )
            except Exception as exc:  # noqa: BLE001 LLM 调用失败即终止
                logger.error("Agent LLM 调用失败: %s", exc)
                return AgentResult(
                    question=question, answer=answer, steps=steps,
                    iterations=iterations, error=str(exc),
                )
            decision = parse_decision(text)
            if decision is None:
                answer = text
                break
            if "answer" in decision:
                answer = decision["answer"]
                break
            tool_name = decision["tool"]
            params = decision["params"]
            tool = self._tools.get(tool_name)
            if tool is None:
                observation = f"工具不存在: {tool_name}"
                steps.append(AgentStep(tool=tool_name, params=params, observation=observation, ok=False))
                messages.append(Message.assistant(text))
                messages.append(Message.user(observation))
                continue
            try:
                observation = await tool.handler(params)
                ok = True
            except Exception as exc:  # noqa: BLE001 单工具失败不中断循环
                observation = f"工具执行失败: {exc}"
                ok = False
            steps.append(AgentStep(tool=tool_name, params=params, observation=observation, ok=ok))
            messages.append(Message.assistant(text))
            messages.append(Message.user(f"工具 {tool_name} 返回：\n{observation}"))
        if not answer:
            answer = "已达最大迭代次数，无法给出最终答案。"
        return AgentResult(question=question, answer=answer, steps=steps, iterations=iterations)
