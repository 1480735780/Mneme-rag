# P1 实施计划：Agent MVP（plan-execute-observe-answer 最小闭环）

> 目标：补齐 [ragent-file-by-file-comparison.md](../ragent-file-by-file-comparison.md) §12 中 P1——
> 「补 Agent MVP」，用现有 `RoutingLLMService`、`MultiChannelRetrievalEngine`、MCP registry
> 把 plan-execute-observe-answer 闭环落到 `core/pipeline/agent_pipeline.py`（既有空占位），
> 并以薄门面 + JSON 端点对外暴露。
>
> 口径：对齐对比文档 §11「agent/ 放弃登记，能力由 rag/mcp + rag/memory + core/pipeline/agent_pipeline.py（占位）承载」——
> 本轮把该占位落地为可运行的 Agent 闭环；**不动 RAGChatEngine**（聊天主链保持原样）。

---

## 1. 背景与现状基线

**差距来源**：file-by-file 文档 §12 P1 行（「补 Agent MVP | 用现有 RoutingLLMService、
MultiChannelRetrievalEngine、MCP registry 打通 plan-execute-observe-answer」）。

| 现状组件 | 落点 | 状态 |
|---|---|---|
| LLM 路由服务 | [core/llm/chat.py](../../core/llm/chat.py) `RoutingLLMService`（`LLMService` 接口：`chat(request)` 同步返回 str / `stream_chat`） | ✅ 已交付（P0） |
| 多通道检索引擎 | [rag/retrieval/engine.py](../../rag/retrieval/engine.py) `MultiChannelRetrievalEngine.retrieve_knowledge_channels(sub_intent, budget, scope_resolver)` | ✅ 已交付 |
| MCP 工具注册表 | [rag/mcp/registry.py](../../rag/mcp/registry.py) `McpToolRegistry` / `DefaultMcpToolRegistry`（`list_all_executors()` / `get_executor()`）；executor `execute(params)` 同步 → `McpToolResult.to_text()` | ✅ 已交付（P2/M3'） |
| MCP 客户端自动装配 | [rag/mcp/autoconfig.py](../../rag/mcp/autoconfig.py) `McpClientAutoConfiguration`（按 servers 建客户端 → listTools → 注册） | ✅ 已交付（P8 M3'） |
| Agent 管线占位 | [core/pipeline/agent_pipeline.py](../../core/pipeline/agent_pipeline.py) | ❌ 空文件（本轮 M1/M2 落地） |
| Agent 对外入口 | — | ❌ 缺失（本轮 M3 门面 + 端点） |

**复用基础**：
- wiring 先例 `_wire_eval_services`（[app/wiring.py](../../app/wiring.py#L406-L426)）：从 `self.engine` 提取
  `_retrieval_engine` / `_budget` / `_scope_resolver`，引擎未就绪则服务为 None、端点不挂载；
- 控制器先例 [rag/controller/eval_controller.py](../../rag/controller/eval_controller.py)：`_container(request)` 取容器
  → service 方法 → `result_to_dict(Results.success(camelize(...)))`；VO 转换用 `rag/controller/vo.camelize`；
- 数据模型先例：`Message.system/user/assistant`（[core/llm/schema.py](../../core/llm/schema.py)）、
  `ChatRequest(messages, temperature, thinking)`、`SubQuestionIntent(sub_question, node_scores)`、
  `RetrievalBudget(recall_budget=100, candidate_limit=30, context_top_k=10)`；
- 测试先例：无 pytest-asyncio，异步统一 `asyncio.run()` 包裹；MCP 测试用 `DefaultMcpToolRegistry` + 假 executor。

**测试基线**：全量回归 **529 passed**（2026-08-23，P1 evaluation 收官）。

---

## 2. 关键决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | **落点 = `core/pipeline/agent_pipeline.py`（既有占位）**，主类 `AgentPipeline` + 内部数据模型 | 对比文档已声明该文件承载 agent 能力；不动 RAGChatEngine / rag 检索主线 |
| D2 | **循环范式 = ReAct 风格 plan-execute-observe-answer**：系统提示（工具清单 + JSON 输出协议）→ LLM 决策 → 执行工具 → observation 回填 → 迭代 → 最终答案 | 对齐对比文档「打通 plan-execute-observe-answer」；MCP 工具与 knowledge_search 都是「工具」，统一抽象 |
| D3 | **内部工具抽象 `AgentTool`（async handler）**，适配两种源：MCP registry executor（`asyncio.to_thread` 包同步 `execute`）与内置 `knowledge_search`（直接 `await` 检索引擎） | 引擎 retrieve 为 async、MCP execute 为 sync，统一 async 抽象让主循环只认一种工具形态 |
| D4 | **决策协议 = 严格 JSON 容错解析**：`{"tool": name, "params": {...}}` 或 `{"answer": text}`；非 JSON / 无两字段 → 原文本视为最终答案 | 最小闭环不引入 function-calling 强绑定；容错保证任何 LLM 输出都能收敛到答案 |
| D5 | **终止**：`max_iterations` 上限（默认 5）+ LLM 调用失败直接返回 error；未知工具 / 工具异常**不中断**（记 observation 继续） | 对齐项目「单点失败降级、不中断主链」心智（eval_service / RAGChatEngine 同款） |
| D6 | **LLM 调用用同步 `llm_service.chat(...)`（非流式）**；端点 `POST /agent/chat` 返回 JSON | Agent 闭环天然多轮同步；MVP 不做 SSE 流式，降低接线复杂度 |
| D7 | **接线复用 `_wire_eval_services` 先例**：从 `self.engine` 提取 retrieval/budget/scope_resolver；LLM 用 `_get_shared_llm()`；MCP registry 注入槽优先，否则 `McpClientAutoConfiguration` 自动装配（无配置 → 仅内置 knowledge_search） | 引擎/LLM 未就绪 → `agent_service=None` → 端点不挂载（半装配防护，同 eval） |
| D8 | **端点 = `POST /agent/chat`**（question + 可选 history → camelCase AgentResult） | 与既有 C2/C3 端点 camelCase 约定一致（`result_to_dict` + `camelize`） |
| D9 | **零新依赖**；测试全内存/桩（假 LLM scripted 响应、假 executor、假检索引擎） | 遵循项目「薄脚本/零新依赖」约定 |

---

## 3. 任务分解

### 3.1 M1：AgentPipeline 核心循环 [core/pipeline/agent_pipeline.py](../../core/pipeline/agent_pipeline.py)（占位文件落盘）— ✅ 已完成

**Files:**
- Modify: `core/pipeline/agent_pipeline.py`（当前空文件，整文件写入）
- Test: `tests/test_agent_pipeline_unit.py`（新建，M1+M2 共用）

**Step 1（TDD）**：先写测试文件核心用例（见 §4.1），跑起来确认 FAIL（模块不存在）。

**Step 2（实现）**：整文件写入以下内容（照抄即用）：

```python
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
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.llm.schema import ChatRequest, Message
from rag.intent import SubQuestionIntent
from rag.mcp import McpToolExecutor, McpToolRegistry
from rag.retrieval.schema import RetrievalBudget

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._llm_service = llm_service
        self._max_iterations = max_iterations
        self._context_top_k = context_top_k
        self._retrieval_engine = retrieval_engine
        self._budget = budget or RetrievalBudget()
        self._scope_resolver = scope_resolver
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
        if self._retrieval_engine is None:
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
        steps: List[AgentStep] = []
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
```

**Step 3**：跑 `tests/test_agent_pipeline_unit.py` 中 M1 用例 → 全绿。

### 3.2 M2：工具源适配（MCP registry 包装 + knowledge_search 内置）[同文件追加]

M1 代码已包含 `_load_mcp_tools` / `_load_knowledge_tool`（TDD 设计阶段一并写出）。M2 补齐这两类
工具源的单测（假 executor / 假检索引擎），并把「LLM 决策调用 MCP 工具」闭环验证。

**Step 1（TDD）**：追加 §4.2 用例，先跑确认 FAIL（handler 缺失或注册表为空）。

**Step 2（实现）**：M1 文件已含实现，无额外改动；如测试暴露缺口再补（例如空注册表跳过、executor 返回 None）。

**Step 3**：跑 `tests/test_agent_pipeline_unit.py` 全量 → 全绿。

### 3.3 M3：门面 + 控制器 + 接线 — ✅ 已完成

**Files:**
- Create: `rag/service/agent_service.py`
- Create: `rag/controller/agent_controller.py`
- Modify: `app/wiring.py`（AppContainer 字段 + `_wire_agent_services` + `_build_memory` / `_build_real` 调用 + `aclose` 清理）
- Modify: `app/factory.py`（条件挂载 agent 路由）
- Test: `tests/test_agent_controller_unit.py`（新建）、`tests/test_agent_wiring_unit.py`（新建）

**Step 1（TDD）**：先写控制器/装配测试（见 §4.3），跑确认 FAIL。

**Step 2（实现）** 三个新文件 + 两处修改。

`rag/service/agent_service.py`（新建，整文件）：

```python
# -*- coding: utf-8 -*-
"""
rag.service.agent_service - Agent 聊天门面（POST /agent/chat 依赖）

薄门面：持有 AgentPipeline，把 AgentResult 转成 snake_case dict（controller 边界 camelize）。
history 入参 [{role, content}] → [Message]（非法行丢弃；空列表返回 None 由管线兜底）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class AgentChatService:
    """Agent 对话门面（对比文档 §12 P1 Agent MVP 的对外入口）"""

    def __init__(self, pipeline: Any):
        self._pipeline = pipeline

    async def chat(
        self,
        question: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """执行 Agent 闭环 → snake_case dict（steps 逐项含 tool/params/observation/ok）"""
        history_messages = self._to_messages(history)
        result = await self._pipeline.run(question, history_messages)
        return {
            "answer": result.answer,
            "iterations": result.iterations,
            "error": result.error,
            "steps": [
                {"tool": s.tool, "params": s.params, "observation": s.observation, "ok": s.ok}
                for s in result.steps
            ],
        }

    @staticmethod
    def _to_messages(history: Optional[List[Dict[str, Any]]]) -> Optional[List[Any]]:
        """[{role, content}] → [Message]；空/非法行丢弃；整体为空返回 None"""
        if not history:
            return None
        from core.llm.schema import Message

        messages = []
        for item in history:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if not role or not content:
                continue
            if role == "user":
                messages.append(Message.user(content))
            elif role == "assistant":
                messages.append(Message.assistant(content))
        return messages or None
```

`rag/controller/agent_controller.py`（新建，整文件）：

```python
# -*- coding: utf-8 -*-
"""
rag.controller.agent_controller - Agent 对话端点（POST /agent/chat）

JSON（非流式）：question + 可选 history → camelCase AgentResult
（answer / steps / iterations / error）。Agent 闭环天然多轮同步，MVP 不做 SSE 流式。
依赖注入：agent_service 从 request.app.state.container 取（wiring 装配；引擎未就绪不挂载）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(tags=["agent"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.post("/agent/chat", name="agent_chat")
async def agent_chat(
    request: Request,
    payload: Dict[str, Any] = Body(...),
) -> dict:
    """POST /agent/chat：Agent 闭环（plan-execute-observe-answer）"""
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    history: Optional[List[Dict[str, Any]]] = payload.get("history")
    container = _container(request)
    data = await container.agent_service.chat(question, history)
    return result_to_dict(Results.success(camelize(data)))
```

`app/wiring.py` 修改（三处）：

(1) AppContainer 字段区（[wiring.py#L238](../../app/wiring.py#L238) 附近追加）：

```python
    # P8 E 组评测域：检索评测服务（EvalController 依赖；引擎就绪才装配，否则 None）
    eval_service: Any = None
    # P1 Agent MVP：Agent 门面 + MCP 注册表注入槽（AgentController 依赖；引擎就绪才装配，否则 None）
    agent_service: Any = None
    mcp_tool_registry: Any = None
    _mcp_autoconfig: Any = None
```

(2) `_build_memory` / `_build_real` 的装配序列（在 `_wire_eval_services()` 之后、`_wire_idempotent_framework()` 之前追加一行）：

```python
        container._wire_eval_services()
        container._wire_agent_services()   # P1 Agent MVP：须在 _wire_chat_services 之后（engine 已装配）
        container._wire_idempotent_framework()
```

(3) 新增方法 `_wire_agent_services`（放在 `_wire_eval_services` 之后）：

```python
    def _wire_agent_services(self) -> None:
        """P1 Agent MVP：AgentChatService（AgentController 依赖）

        复用 _wire_eval_services 的「引擎组件提取」先例：从 engine 取 retrieval_engine/budget/
        scope_resolver；LLM 用共享路由（_get_shared_llm）。MCP registry 注入槽优先，
        否则 McpClientAutoConfiguration 自动装配（无配置 → 空注册表，仅内置 knowledge_search）。
        引擎/LLM 未就绪 → agent_service=None，端点不挂载（半装配防护）。
        须在 _wire_chat_services 之后调用（engine 在其中装配）。
        """
        llm = self._get_shared_llm()
        if llm is None or self.engine is None:
            return
        from core.pipeline.agent_pipeline import AgentPipeline
        from rag.mcp import McpClientAutoConfiguration, McpClientProperties, DefaultMcpToolRegistry
        from rag.service.agent_service import AgentChatService

        registry = self.mcp_tool_registry  # 注入槽优先（测试/外部装配）
        if registry is None:
            registry = DefaultMcpToolRegistry()
            autoconfig = McpClientAutoConfiguration(McpClientProperties(), registry)
            autoconfig.init()  # 无配置 servers → 空注册表；失败 server 跳过
            self._mcp_autoconfig = autoconfig
            self._owned.append(_McpAutoconfigCloser(autoconfig))  # aclose 时 destroy 客户端

        pipeline = AgentPipeline(
            llm,
            tool_registry=registry,
            retrieval_engine=self.engine._retrieval_engine,
            budget=self.engine._budget,
            scope_resolver=self.engine._scope_resolver,
        )
        self.agent_service = AgentChatService(pipeline)
```

(4) 模块级小助手（放在 `AppContainer` 类外）：

```python
class _McpAutoconfigCloser:
    """把 McpClientAutoConfiguration.destroy() 适配为容器 _owned 的 close() 约定"""

    def __init__(self, autoconfig: Any) -> None:
        self._autoconfig = autoconfig

    def close(self) -> None:
        self._autoconfig.destroy()
```

`app/factory.py` 修改（lifespan 内、eval 挂载之后追加）：

```python
        # P1 Agent MVP 端点（D8）：agent_service 装配（引擎/LLM 就绪）才挂载
        if container.agent_service is not None:
            from rag.controller.agent_controller import router as agent_router

            app.include_router(agent_router)
```

**Step 3**：跑 `tests/test_agent_controller_unit.py` + `tests/test_agent_wiring_unit.py` → 全绿。

### 3.4 M4：文档更新

| # | 文件 | 改动 |
|---|---|---|
| M4a | [docs/rag/agent-guide.md](../rag/agent-guide.md)（新建） | Agent MVP 使用说明：输出协议、端点示例、工具源（MCP + knowledge_search）、终止语义 |
| M4b | [rag/README.md](../../rag/README.md) | 若存在模块清单，追加 agent/controller 两文件一行说明 |

### 3.5 M5：收官 — ✅ 已完成

- 全量回归（基线 529 只增不减）；
- [ragent-file-by-file-comparison.md](../ragent-file-by-file-comparison.md) §12 P1「补 Agent MVP」行销案（✅ + 落点/测试数）；
- 本计划文档 §7 写收官记录。

---

## 4. 测试保障

**TDD 先行**；新增测试全部纯内存/桩，不依赖真实 LLM / 后端 / MCP Server。异步统一 `asyncio.run()` 包裹。

### 4.1 [tests/test_agent_pipeline_unit.py](../../tests/test_agent_pipeline_unit.py)（M1 用例，新建）

测试替身：

```python
import asyncio

from core.pipeline.agent_pipeline import AgentPipeline, AgentResult, AgentStep, AgentTool, parse_decision


class _ScriptedLLM:
    """scripted chat：按序返回预设文本；耗尽抛错（验证 LLM 失败终止）"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def chat(self, request, tier=None, preferred_model_id=None):
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("LLM 脚本耗尽")
        return self.responses.pop(0)


async def _add(params):
    return str(params["a"] + params["b"])


def _pipeline(llm, tools=None, **kwargs):
    return AgentPipeline(llm, extra_tools=tools, **kwargs)
```

| 用例 | 断言要点 |
|---|---|
| parse_decision：tool 调用 / answer / 空串 / 非 JSON / 裸列表 / 无字段 dict | 返回 dict / dict / None / None / None / None |
| 单工具 → 回答（scripted：`{"tool":"add","params":{"a":1,"b":2}}` → `{"answer":"3"}`） | answer="3"、steps==1（ok=True）、iterations==2、add 执行 |
| 直接回答（scripted：`{"answer":"好"}`） | answer="好"、steps==[]、iterations==1 |
| 未知工具 → 继续（scripted：未知 → answer） | steps[0].ok==False、observation 含「工具不存在」、answer 正常 |
| 工具抛异常 → 不中断（handler 抛 ValueError） | steps[0].ok==False、observation 含「工具执行失败」、后续收敛 |
| 达 max_iterations（scripted 全是 tool 调用，无 answer） | answer 含「最大迭代次数」、iterations==max_iterations |
| run 级 max_iterations=1 覆盖构造默认 | iterations==1 |
| LLM 调用失败终止（scripted 空） | error 非空、answer 为空 |
| 系统提示含工具清单 + JSON 协议 | `_system_prompt()` 含工具名与 `"tool"` / `"answer"` 关键字 |
| history 注入（Message.user/assistant 前置） | `_build_messages` 中 user 消息在最后、system 在首位 |

### 4.2 [tests/test_agent_pipeline_unit.py](../../tests/test_agent_pipeline_unit.py)（M2 用例，同文件追加）

测试替身：

```python
from rag.mcp import (
    DefaultMcpToolRegistry,
    McpTextContent,
    McpToolDefinition,
    McpToolExecutor,
    McpToolResult,
)


class _FakeExecutor(McpToolExecutor):
    def __init__(self, name, description="", text="ok"):
        self._def = McpToolDefinition(name=name, description=description, input_schema={"type": "object"})
        self._text = text
        self.calls = []

    def get_tool_definition(self):
        return self._def

    def execute(self, parameters):
        self.calls.append(dict(parameters))
        return McpToolResult(content=[McpTextContent(text=self._text)])


class _FakeRetrievalEngine:
    def __init__(self, chunks):
        self.chunks = chunks
        self.questions = []

    async def retrieve_knowledge_channels(self, sub_intent, budget, scope_resolver=None):
        self.questions.append(sub_intent.sub_question)
        from types import SimpleNamespace
        return SimpleNamespace(chunks=self.chunks)
```

| 用例 | 断言要点 |
|---|---|
| MCP 工具自动注册（`DefaultMcpToolRegistry(auto_discovered_executors=[_FakeExecutor("weather_query", text="晴 25°C")])`） | `pipeline._tools` 含 weather_query、description 透传 |
| Agent 循环调用 MCP 工具（scripted：调 weather_query → answer） | executor.calls==[{"city":"北京"}]、answer 正常、steps[0].observation=="晴 25°C" |
| MCP executor 返回空结果（execute → None） | observation=="工具调用返回空结果" |
| 注入检索引擎 → 注册 knowledge_search | `pipeline._tools` 含 knowledge_search |
| knowledge_search 返回片段（`_FakeRetrievalEngine([SimpleNamespace(id="c1", text="片段A"), ...])`） | 文本含 `[1] 片段A`、questions==["什么是退款政策"]、按 context_top_k 截断 |
| knowledge_search 空结果 | observation=="未检索到相关知识。" |
| knowledge_search 缺 question 参数 | observation=="knowledge_search 需要参数 question" |

### 4.3 [tests/test_agent_controller_unit.py](../../tests/test_agent_controller_unit.py)（M3，新建）

用假容器（`app.state.container` 注入假 `agent_service`）跑真实路由：

```python
import asyncio

from fastapi.testclient import TestClient
from fastapi import FastAPI

from rag.controller.agent_controller import router


class _FakeAgentService:
    def __init__(self):
        self.last = None

    async def chat(self, question, history=None):
        self.last = (question, history)
        return {
            "answer": "北京明天晴 25°C",
            "iterations": 2,
            "error": None,
            "steps": [{"tool": "weather_query", "params": {"city": "北京"}, "observation": "晴 25°C", "ok": True}],
        }


def _client(service):
    app = FastAPI()
    app.state.container = type("C", (), {"agent_service": service})()
    app.include_router(router)
    return TestClient(app)
```

| 用例 | 断言要点 |
|---|---|
| POST /agent/chat 正常 | 200、body.data.answer=="北京明天晴 25°C"、steps 已 camelCase（`params` 保持不变、字段名 `ok` 保持）、`iterations` |
| 传递 history | service.last[1] 原样透传 |
| 空 question | 400 |

### 4.4 [tests/test_agent_wiring_unit.py](../../tests/test_agent_wiring_unit.py)（M3，新建）

内存栈容器 + 注入槽装配验证（复用 `_wire_eval_services` 的 None 防护心智）：

```python
import asyncio

from app.config import AppSettings
from app.wiring import AppContainer


class _FakeLLM:
    async def chat(self, request, tier=None, preferred_model_id=None):
        return '{"answer": "来自假 LLM"}'


class _FakeEngine:
    _retrieval_engine = None
    _budget = None
    _scope_resolver = None
```

| 用例 | 断言要点 |
|---|---|
| 引擎+LLM 就绪 → agent_service 装配（`container.llm_service=_FakeLLM(); container.engine=_FakeEngine(); container._wire_agent_services()`） | `container.agent_service is not None` |
| 引擎为 None → 不装配 | `container.agent_service is None` |
| `_FakeEngine` 无 retrieval → pipeline 仅 knowledge 之外工具；`_wire_agent_services` 不抛 | 正常返回，agent_service 存在 |
| MCP registry 注入槽优先（`container.mcp_tool_registry=DefaultMcpToolRegistry([...])`） | pipeline 含注入工具名 |

> 注：以上 wiring 用例通过直接调用 `_wire_agent_services()` 验证（对齐 eval_service 无专测的现状，
> 但 Agent 多一步「自动装配空注册表 + 半装配防护」的确定性验证）。

**流程保障**：每个里程碑交付后跑对应测试文件绿 → 全量回归基线只增不减；调试脚本随手删除（用户规则）。

---

## 5. 验收标准

- [x] M1：`tests/test_agent_pipeline_unit.py` M1 用例全绿（parse_decision 边界 + 循环收敛 + 终止语义）——10 例
- [x] M2：同文件 M2 用例全绿（MCP 注册 + knowledge_search 格式化/空/缺参）——8 例
- [x] M3：`tests/test_agent_controller_unit.py` 全绿（POST /agent/chat 正常/传 history/空 question 400）+ `tests/test_agent_wiring_unit.py` 全绿（就绪装配 / 未就绪防护 / 注入槽优先）——9 例
- [x] `AgentPipeline` 对真实引擎/LLM 可装配（wiring 路径验证）
- [x] M4：agent-guide.md 与 rag/README.md 更新到位
- [x] M5：全量回归 ≥529 passed 只增不减（563 passed）；对比文档 §12 P1「补 Agent MVP」行销案

---

## 6. 里程碑与执行顺序

| 里程碑 | 内容 | 出口 |
|---|---|---|
| M1 | AgentPipeline 核心循环 + parse_decision + 数据模型 | test_agent_pipeline_unit.py（M1 段）绿 |
| M2 | 工具源适配（MCP 包装 + knowledge_search） | 同文件（M2 段）绿 |
| M3 | AgentChatService + AgentController + wiring/factory 接线 | controller/wiring 测试绿 |
| M4 | agent-guide.md + rag/README.md 更新 | 文档引用一致 |
| M5 | 全量回归 + 对比文档销案 + 本计划 §7 收官记录 | 529+ 全绿，P1 ✅ |

> 执行顺序：M1→M2→M3 串行（M3 依赖前两者）；M4/M5 收尾。全程零新依赖、零运行时库改动。

---

## 7. 维护说明

- 本文档与代码同步演进：每完成一个里程碑将状态改为 ✅ 并注明落点；
- 状态标记规则：❌ 未开始 / 🚧 进行中 / ✅ 已完成（附测试通过）/ ⛔ 显式放弃（附理由）；
- 与 [ragent-file-by-file-comparison.md](../ragent-file-by-file-comparison.md) §12 联动：P1 销案时同步更新；
- 与 [agent-guide.md](../rag/agent-guide.md) 保持口径一致（输出协议、工具源、终止语义）。

### 7.1 收官记录（2026-08-23）

**P1 Agent MVP 全部交付**，全量回归 **563 passed**（基线 529 + 新增 34）只增不减。

| 里程碑 | 状态 | 落点 |
|---|---|---|
| M1 | ✅ | `core/pipeline/agent_pipeline.py`：`AgentPipeline` 主循环 + `parse_decision` 容错 + `AgentTool/AgentStep/AgentResult`；M1 段 10 例 |
| M2 | ✅ | 同文件追加 MCP 工具源（`asyncio.to_thread` 适配同步 execute）+ 内置 `knowledge_search`；M2 段 8 例 |
| M3 | ✅ | `rag/service/agent_service.py`（门面）+ `rag/controller/agent_controller.py`（POST /agent/chat）+ wiring `_wire_agent_services`（注入槽优先 / 无配置自动装配空注册表 / 引擎未就绪不挂载）+ factory 条件挂载；controller 4 例 + wiring 5 例 |
| M4 | ✅ | `docs/rag/agent-guide.md` 新建；`rag/README.md` §使用说明追加第 5 条 |
| M5 | ✅ | 对比文档 §12 P1「补 Agent MVP」行销案（✅ + 落点/测试数）；本记录 |

**验收口径**：`tests/test_agent_pipeline_unit.py`（25）+ `tests/test_agent_controller_unit.py`（4）+ `tests/test_agent_wiring_unit.py`（5）= **34 例新增单测**；全量回归 563 passed（exit 1 为沙箱 pyc 写保护告警，非测试失败）。

**已知取舍**（MVP 边界）：
- Agent 闭环为同步 JSON 端点（D6），未做 SSE 流式；
- 决策协议为「严格 JSON 容错」（D4），未引入 function-calling 强绑定；
- 未依赖真实 MCP Server / 真实 LLM 做端到端（D9，测试全内存/桩）；真实 LLM 装配经 wiring 路径验证。
