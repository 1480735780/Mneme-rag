# -*- coding: utf-8 -*-
"""
P1 Agent MVP：AgentPipeline 核心循环测试（plan-execute-observe-answer 最小闭环）

覆盖：
    M1（§4.1）：
        - parse_decision 边界（tool / answer / 空串 / 非 JSON / 裸列表 / 无字段 dict）
        - 单工具 → 回答（observations 回填、iterations 计数）
        - 直接回答（无工具调用）
        - 未知工具 → 不中断继续
        - 工具抛异常 → 不中断继续
        - 达 max_iterations 终止（含 run 级覆盖）
        - LLM 调用失败 → 返回 error
        - 系统提示含工具清单 + JSON 输出协议
        - history 注入（user 消息在最后、system 在首位）
    M2（§4.2）：
        - MCP 工具自动注册（DefaultMcpToolRegistry + 假 executor）
        - Agent 循环调用 MCP 工具（asyncio.to_thread 适配同步 execute）
        - MCP executor 返回空结果
        - 注入检索引擎 → 注册内置 knowledge_search
        - knowledge_search 返回片段（context_top_k 截断）/ 空结果 / 缺参数

全部纯内存/桩：scripted LLM、假 executor、假检索引擎；异步统一 asyncio.run() 包裹。
"""
import asyncio
from types import SimpleNamespace

from core.pipeline.agent_pipeline import AgentPipeline, AgentTool, parse_decision
from core.llm.schema import Message
from rag.mcp import (
    DefaultMcpToolRegistry,
    McpTextContent,
    McpToolDefinition,
    McpToolExecutor,
    McpToolResult,
)


# ==================== 测试替身 ====================


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


class _FakeExecutor(McpToolExecutor):
    """假 MCP 执行器：记录调用参数并返回固定文本"""

    def __init__(self, name, description="", text="ok"):
        self._def = McpToolDefinition(
            name=name, description=description, input_schema={"type": "object"}
        )
        self._text = text
        self.calls = []

    def get_tool_definition(self):
        return self._def

    def execute(self, parameters):
        self.calls.append(dict(parameters))
        if self._text is None:
            return None
        return McpToolResult(content=[McpTextContent(text=self._text)])


class _NoneExecutor(McpToolExecutor):
    """假 MCP 执行器：execute 返回 None（验证空结果兜底）"""

    def __init__(self, name="empty_tool"):
        self._def = McpToolDefinition(name=name, description="", input_schema={"type": "object"})

    def get_tool_definition(self):
        return self._def

    def execute(self, parameters):
        return None


class _FakeRetrievalEngine:
    """假检索引擎：按 sub_question 记录并返回 chunks"""

    def __init__(self, chunks):
        self.chunks = chunks
        self.questions = []

    async def retrieve_knowledge_channels(self, sub_intent, budget, scope_resolver=None):
        self.questions.append(sub_intent.sub_question)
        return SimpleNamespace(chunks=self.chunks)


# ==================== M1：核心循环 ====================


class TestParseDecision:
    def test_tool_call(self):
        d = parse_decision('{"tool": "add", "params": {"a": 1}}')
        assert d == {"tool": "add", "params": {"a": 1}}

    def test_answer(self):
        d = parse_decision('{"answer": "你好"}')
        assert d == {"answer": "你好"}

    def test_empty_string(self):
        assert parse_decision("") is None
        assert parse_decision("   ") is None

    def test_non_json(self):
        assert parse_decision("直接回答，不是 JSON") is None

    def test_bare_list(self):
        assert parse_decision("[1, 2, 3]") is None

    def test_no_recognized_field(self):
        assert parse_decision('{"foo": "bar"}') is None


class TestPipelineLoop:
    def test_single_tool_then_answer(self):
        llm = _ScriptedLLM([
            '{"tool": "add", "params": {"a": 1, "b": 2}}',
            '{"answer": "3"}',
        ])
        pipe = _pipeline(llm, tools=[AgentTool("add", "加法", _add)])
        result = _run(pipe, "1+2=?")
        assert result.answer == "3"
        assert len(result.steps) == 1
        assert result.steps[0].tool == "add"
        assert result.steps[0].ok is True
        assert result.steps[0].observation == "3"
        assert result.iterations == 2
        assert result.error is None

    def test_direct_answer_no_tool(self):
        llm = _ScriptedLLM(['{"answer": "好"}'])
        pipe = _pipeline(llm)
        result = _run(pipe, "你好")
        assert result.answer == "好"
        assert result.steps == []
        assert result.iterations == 1

    def test_plain_text_treated_as_answer(self):
        # 非 JSON 输出 → 原文本视为最终答案（D4 容错）
        llm = _ScriptedLLM(["好的，这是一个普通回答"])
        pipe = _pipeline(llm)
        result = _run(pipe, "你好")
        assert result.answer == "好的，这是一个普通回答"
        assert result.iterations == 1

    def test_unknown_tool_continues(self):
        llm = _ScriptedLLM([
            '{"tool": "not_exist", "params": {}}',
            '{"answer": "已处理"}',
        ])
        pipe = _pipeline(llm, tools=[AgentTool("add", "加法", _add)])
        result = _run(pipe, "测试")
        assert result.steps[0].ok is False
        assert "工具不存在" in result.steps[0].observation
        assert result.answer == "已处理"
        assert result.iterations == 2

    def test_tool_exception_does_not_abort(self):
        async def _boom(params):
            raise ValueError("boom")

        llm = _ScriptedLLM([
            '{"tool": "boom", "params": {}}',
            '{"answer": "已降级"}',
        ])
        pipe = _pipeline(llm, tools=[AgentTool("boom", "会炸", _boom)])
        result = _run(pipe, "测试")
        assert result.steps[0].ok is False
        assert "工具执行失败" in result.steps[0].observation
        assert "boom" in result.steps[0].observation
        assert result.answer == "已降级"
        assert result.error is None

    def test_max_iterations_reached(self):
        # scripted 全是 tool 调用、无 answer → 达上限
        llm = _ScriptedLLM([
            '{"tool": "add", "params": {"a": 1, "b": 1}}',
            '{"tool": "add", "params": {"a": 1, "b": 2}}',
            '{"tool": "add", "params": {"a": 1, "b": 3}}',
            '{"tool": "add", "params": {"a": 1, "b": 4}}',
            '{"tool": "add", "params": {"a": 1, "b": 5}}',
        ])
        pipe = _pipeline(llm, tools=[AgentTool("add", "加法", _add)], max_iterations=5)
        result = _run(pipe, "累加")
        assert result.answer == "已达最大迭代次数，无法给出最终答案。"
        assert result.iterations == 5
        assert len(result.steps) == 5

    def test_run_level_max_iterations_overrides(self):
        llm = _ScriptedLLM(['{"tool": "add", "params": {"a": 1, "b": 1}}'])
        pipe = _pipeline(llm, tools=[AgentTool("add", "加法", _add)], max_iterations=5)
        result = _run(pipe, "累加", max_iterations=1)
        assert result.iterations == 1
        assert result.answer == "已达最大迭代次数，无法给出最终答案。"

    def test_llm_failure_returns_error(self):
        # scripted 空 → 首次 chat 即抛 RuntimeError → error 返回
        llm = _ScriptedLLM([])
        pipe = _pipeline(llm)
        result = _run(pipe, "你好")
        assert result.error is not None
        assert "LLM 脚本耗尽" in result.error
        assert result.answer == ""
        assert result.iterations == 1


class TestPromptAndHistory:
    def test_system_prompt_contains_tools_and_protocol(self):
        pipe = _pipeline(
            _ScriptedLLM([]),
            tools=[AgentTool("add", "加法", _add), AgentTool("mul", "乘法", _add)],
        )
        prompt = pipe._system_prompt()
        assert "add" in prompt
        assert "mul" in prompt
        assert '"tool"' in prompt
        assert '"answer"' in prompt

    def test_build_messages_order(self):
        pipe = _pipeline(_ScriptedLLM([]))
        history = [Message.user("之前的问题"), Message.assistant("之前的回答")]
        messages = pipe._build_messages("现在的问题", history)
        assert messages[0].role.value == "system"
        assert messages[-1].role.value == "user"
        assert messages[-1].content == "现在的问题"
        assert [m.content for m in messages[1:-1]] == ["之前的问题", "之前的回答"]


# ==================== M2：工具源适配 ====================


class TestMcpTools:
    def test_mcp_tool_auto_registered(self):
        executor = _FakeExecutor("weather_query", description="查询天气", text="晴 25°C")
        registry = DefaultMcpToolRegistry(auto_discovered_executors=[executor])
        pipe = _pipeline(_ScriptedLLM([]), tool_registry=registry)
        assert "weather_query" in pipe._tools
        assert pipe._tools["weather_query"].description == "查询天气"

    def test_loop_calls_mcp_tool(self):
        executor = _FakeExecutor("weather_query", description="查询天气", text="晴 25°C")
        registry = DefaultMcpToolRegistry(auto_discovered_executors=[executor])
        llm = _ScriptedLLM([
            '{"tool": "weather_query", "params": {"city": "北京"}}',
            '{"answer": "北京明天晴 25°C"}',
        ])
        pipe = _pipeline(llm, tool_registry=registry)
        result = _run(pipe, "北京天气？")
        assert executor.calls == [{"city": "北京"}]
        assert result.answer == "北京明天晴 25°C"
        assert result.steps[0].observation == "晴 25°C"
        assert result.steps[0].ok is True

    def test_mcp_executor_returns_none(self):
        registry = DefaultMcpToolRegistry(auto_discovered_executors=[_NoneExecutor("empty_tool")])
        llm = _ScriptedLLM([
            '{"tool": "empty_tool", "params": {}}',
            '{"answer": "完成"}',
        ])
        pipe = _pipeline(llm, tool_registry=registry)
        result = _run(pipe, "测试")
        assert result.steps[0].observation == "工具调用返回空结果"

    def test_empty_registry_no_crash(self):
        registry = DefaultMcpToolRegistry()
        pipe = _pipeline(_ScriptedLLM([]), tool_registry=registry)
        assert "weather_query" not in pipe._tools


class TestKnowledgeSearch:
    def test_registered_when_engine_injected(self):
        engine = _FakeRetrievalEngine([])
        pipe = _pipeline(_ScriptedLLM([]), retrieval_engine=engine)
        assert "knowledge_search" in pipe._tools

    def test_returns_chunks_with_index(self):
        engine = _FakeRetrievalEngine([
            SimpleNamespace(id="c1", text="片段A"),
            SimpleNamespace(id="c2", text="片段B"),
        ])
        pipe = _pipeline(_ScriptedLLM([]), retrieval_engine=engine)
        result = _run(pipe, "什么是退款政策")
        # 直接调用内置工具走 LLM 决策协议：先给工具调用
        llm_script = _ScriptedLLM(['{"answer": "见知识库"}'])
        pipe2 = _pipeline(llm_script, retrieval_engine=engine)
        # 验证 _knowledge_search 直接调用
        obs = _run_tool(pipe2, "knowledge_search", {"question": "什么是退款政策"})
        assert "[1] 片段A" in obs
        assert "[2] 片段B" in obs
        assert engine.questions == ["什么是退款政策"]

    def test_context_top_k_truncation(self):
        chunks = [SimpleNamespace(id=f"c{i}", text=f"片段{i}") for i in range(10)]
        engine = _FakeRetrievalEngine(chunks)
        pipe = _pipeline(_ScriptedLLM([]), retrieval_engine=engine, context_top_k=3)
        obs = _run_tool(pipe, "knowledge_search", {"question": "q"})
        assert "[4] 片段4" not in obs
        assert obs.count("[") >= 3

    def test_empty_result(self):
        engine = _FakeRetrievalEngine([])
        pipe = _pipeline(_ScriptedLLM([]), retrieval_engine=engine)
        obs = _run_tool(pipe, "knowledge_search", {"question": "q"})
        assert obs == "未检索到相关知识。"

    def test_missing_question_param(self):
        engine = _FakeRetrievalEngine([])
        pipe = _pipeline(_ScriptedLLM([]), retrieval_engine=engine)
        obs = _run_tool(pipe, "knowledge_search", {})
        assert obs == "knowledge_search 需要参数 question"


# ==================== 工具 ====================


def _run(pipe, question, **kwargs):
    return asyncio.run(pipe.run(question, **kwargs))


def _run_tool(pipe, name, params):
    async def _go():
        return await pipe._tools[name].handler(params)

    return asyncio.run(_go())
