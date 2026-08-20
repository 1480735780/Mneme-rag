"""
Prompt 场景选择与 Prompt 装配（对应 ragent PromptScene + PromptContext + RAGPromptService + AgentPrompt*）

职责划分：
    - PromptScene：Prompt 构建场景枚举，根据检索来源（知识库 / MCP）确定系统提示词模板。
    - PromptContext：一次 RAG 请求中用于组装提示词的全部输入数据（已格式化的上下文 + 意图列表）。
    - PromptPlan / PromptBuildPlan：内部规划载体，决定用哪个基础模板与场景。
    - AgentPromptSlot：智能体提示词槽位，是槽位元数据的唯一权威源（展示名/分组/生效模式/
      不生效原因/必填占位符，与 Java 构造参数一一对应）。
    - AgentPromptResolver：智能体提示词解析器。Java 侧从 DB（agent_profile + agent_prompt）叠加
      内置/激活智能体并走 Redis 缓存；Python 提供 ABC 接口 + 内存版 StaticAgentPromptResolver
      （注入 dict 即生效）+ DB 数据源版 DatabaseAgentPromptResolver（agent_resolver.py，叠加回落）。
    - AgentPromptCacheManager：解析结果缓存。Java 侧是 Redis（1 小时过期）；Python 进程内版保持
      同一语义：命中直接返回、未命中返回 None、clear 后强制重载；Redis 版
      RedisAgentPromptCacheManager 见 agent_resolver.py（TTL 1h，经 5.0 CacheManager 抽象）。
    - RAGPromptService：RAG Prompt 编排服务。按场景（KB / MCP / Mixed）选模板，组装 system +
      history + 证据 + 问题 的消息序列；KB 场景且引用开关打开时把行内引用规则追加到系统提示词后。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.prompt.PromptScene
    - com.nageoffer.ai.ragent.rag.core.prompt.PromptContext
    - com.nageoffer.ai.ragent.rag.core.prompt.PromptPlan
    - com.nageoffer.ai.ragent.rag.core.prompt.PromptBuildPlan
    - com.nageoffer.ai.ragent.rag.core.prompt.AgentPromptSlot
    - com.nageoffer.ai.ragent.rag.core.prompt.AgentPromptResolver
    - com.nageoffer.ai.ragent.rag.core.prompt.AgentPromptCacheManager
    - com.nageoffer.ai.ragent.rag.core.prompt.RAGPromptService
    - com.nageoffer.ai.ragent.rag.constant.RAGConstant.ANSWER_CITATION_RULES_PROMPT_PATH
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from core.llm.schema import Message
from rag.intent.model import NodeScore
from rag.prompt.formatter import (
    CONTEXT_FORMAT_PATH,
    PromptTemplateLoader,
    PromptTemplateUtils,
)

# 行内引用规则模板路径（对应 Java RAGConstant.ANSWER_CITATION_RULES_PROMPT_PATH）
ANSWER_CITATION_RULES_PROMPT_PATH = "prompt/answer-citation-rules.st"


class PromptScene(Enum):
    """Prompt 构建场景枚举，根据检索来源（知识库 / MCP）确定系统提示词模板（对应 Java PromptScene）"""

    KB_ONLY = "kb_only"    # 仅命中知识库检索
    MCP_ONLY = "mcp_only"  # 仅命中 MCP 工具调用
    MIXED = "mixed"        # 同时命中知识库和 MCP
    EMPTY = "empty"        # 无任何检索命中


@dataclass
class PromptContext:
    """
    Prompt 构建上下文，封装一次 RAG 请求中用于组装提示词的全部输入数据（对应 Java PromptContext）

    Attributes:
        question:              用户原始问题
        mcp_context:           MCP 工具调用返回的上下文文本（已格式化）
        kb_context:            知识库检索返回的上下文文本（已格式化）
        mcp_intents:           MCP 通道命中的意图及其得分列表
        kb_intents:            知识库通道命中的意图及其得分列表
        retrieved_intent_ids:  有明确文档归属的意图 ID
    """

    question: Optional[str] = None
    mcp_context: Optional[str] = None
    kb_context: Optional[str] = None
    mcp_intents: List[NodeScore] = field(default_factory=list)
    kb_intents: List[NodeScore] = field(default_factory=list)
    retrieved_intent_ids: Set[str] = field(default_factory=set)

    def has_mcp(self) -> bool:
        """是否包含 MCP 上下文（对应 Java hasMcp）"""
        return bool(self.mcp_context and self.mcp_context.strip())

    def has_kb(self) -> bool:
        """是否包含知识库上下文（对应 Java hasKb）"""
        return bool(self.kb_context and self.kb_context.strip())


@dataclass
class PromptPlan:
    """
    单侧（KB 或 MCP）意图到基础模板的规划结果（对应 Java PromptPlan）

    Attributes:
        retained_intents: 用于选择模板的候选意图（已按意图 ID 去重、已按归属过滤）
        base_template:    选用的基模板（单意图且有模板才会有值，否则为 None 表示用默认模板）
    """

    retained_intents: List[NodeScore]
    base_template: Optional[str] = None


@dataclass
class PromptBuildPlan:
    """
    Prompt 构建规划（对应 Java PromptBuildPlan）：确定场景与最终生效的基础模板
    """

    scene: PromptScene
    base_template: Optional[str] = None
    mcp_context: Optional[str] = None
    kb_context: Optional[str] = None
    question: Optional[str] = None


class OrchestrationMode(Enum):
    """
    执行架构档位（对应 Java OrchestrationMode，由引擎类型配置指定）

    属部署级决策（切换需重启，且 AGENT 依赖外部 ReAct 服务存活），因此不开放后台切换。
    """

    WORKFLOW = "workflow"  # v1 编排管线：意图分类 → 检索 → 合成，链路确定、延迟低
    AGENT = "agent"        # v2 ReAct 架构：主 Agent 决策，RAG 管线降级为其中一个 Tool

    @classmethod
    def of(cls, value: Optional[str]) -> "OrchestrationMode":
        """解析配置值，大小写不敏感，无法识别时回落 WORKFLOW（对应 Java of）"""
        if not value or not value.strip():
            return cls.WORKFLOW
        normalized = value.strip().lower()
        for mode in cls:
            if mode.value == normalized:
                return mode
        return cls.WORKFLOW


class SlotGroup(Enum):
    """槽位控制台分栏（对应 Java AgentPromptSlot.Group），按生效范围而非历史归属划分"""

    WORKFLOW = "WorkFlow 专属"
    AGENT = "Agent 专属"
    COMMON = "通用"


class AgentPromptSlot(Enum):
    """
    智能体提示词槽位，是槽位元数据的唯一权威源（对应 Java AgentPromptSlot）

    槽位按功能命名而非按架构命名：生效范围会随 v1/v2 演进变化，不编码进标识符。

    成员元数据（与 Java 构造参数一一对应）：
        display_name:          控制台展示名
        group:                 控制台分栏（SlotGroup）
        effective_modes:       生效的编排模式集合
        inactive_reason:       未生效时展示给管理员的原因，两种架构都生效的槽位为 None
        required_placeholders: 必须出现的占位符，缺失会让下游规则静默失效，故在保存时拒绝
    """

    SYSTEM_CHAT = (
        "闲聊 / 关于助手",
        SlotGroup.WORKFLOW,
        frozenset({OrchestrationMode.WORKFLOW}),
        "Agent 模式下由主 Agent 直接应答",
        frozenset(),
    )
    MCP_ANSWER = (
        "MCP 问答",
        SlotGroup.WORKFLOW,
        frozenset({OrchestrationMode.WORKFLOW}),
        "Agent 模式下改用原生工具调用，无独立的数据合成环节",
        frozenset(),
    )
    MIXED_ANSWER = (
        "混合问答",
        SlotGroup.WORKFLOW,
        frozenset({OrchestrationMode.WORKFLOW}),
        "Agent 模式下由主 Agent 综合多个工具的结果",
        frozenset(),
    )
    AGENT_MAIN = (
        "Agent 人设",
        SlotGroup.AGENT,
        frozenset({OrchestrationMode.AGENT}),
        "WorkFlow 模式不经过 ReAct 架构",
        frozenset(),
    )
    # 两种架构共用：WorkFlow 下由主链路合成，Agent 下由 RAG Tool 内部合成
    KB_ANSWER = (
        "知识库问答",
        SlotGroup.COMMON,
        frozenset({OrchestrationMode.WORKFLOW, OrchestrationMode.AGENT}),
        None,
        frozenset(),
    )
    CONVERSATION_SUMMARY = (
        "会话压缩",
        SlotGroup.COMMON,
        frozenset({OrchestrationMode.WORKFLOW, OrchestrationMode.AGENT}),
        None,
        frozenset({"{summary_max_chars}"}),
    )
    RECOMMENDED_QUESTIONS = (
        "推荐问题",
        SlotGroup.COMMON,
        frozenset({OrchestrationMode.WORKFLOW, OrchestrationMode.AGENT}),
        None,
        frozenset({"{chunks}", "{count}", "{question}", "{answer}"}),
    )

    def __init__(
        self,
        display_name: str,
        group: SlotGroup,
        effective_modes: "frozenset[OrchestrationMode]",
        inactive_reason: Optional[str],
        required_placeholders: "frozenset[str]",
    ):
        self.display_name = display_name
        self.group = group
        self.effective_modes = effective_modes
        self.inactive_reason = inactive_reason
        self.required_placeholders = required_placeholders

    def is_effective_in(self, mode: OrchestrationMode) -> bool:
        """当前架构下该槽位是否被读取（对应 Java isEffectiveIn）"""
        return mode in self.effective_modes

    @classmethod
    def effective_in(cls, mode: OrchestrationMode) -> List["AgentPromptSlot"]:
        """当前架构下真正会被读取的槽位，控制台拿它当覆盖率的分母（对应 Java effectiveIn）"""
        return [slot for slot in cls if slot.is_effective_in(mode)]

    @classmethod
    def find(cls, key: Optional[str]) -> Optional["AgentPromptSlot"]:
        """按槽位名查找（大小写不敏感），未找到返回 None（对应 Java find → Optional）"""
        if not key:
            return None
        for slot in cls:
            if slot.name.lower() == key.lower():
                return slot
        return None


# ==================== 内置默认槽位模板（对齐 Java 内置智能体播种的默认提示词） ====================
#
# 背景：Java 的内置默认来自「数据初始化时播种的 builtin 智能体 + 其 prompt 行」，DB 数据源缺失时
# 回落空串。Python MVP 无数据初始化器 → 这里以**代码级内置默认**兜底，保证无配置时槽位不落空
# （尤其 M4 推荐追问链路 RECOMMENDED_QUESTIONS，空 prompt 会使 LLM 生成退化）。
# 语义：仅对**已声明默认值**的槽位生效；未声明默认值的槽位仍按原逻辑回落空串。
DEFAULT_AGENT_PROMPTS: Dict[str, str] = {
    AgentPromptSlot.RECOMMENDED_QUESTIONS.name: (
        "你是智能提问助手。请基于用户的原始问题、模型给出的答案以及可选检索片段，"
        "生成 {count} 个用户最可能继续追问的问题。\n"
        "要求：\n"
        "1. 只输出一个 JSON 字符串数组，不要任何解释或代码围栏。\n"
        "2. 每个问题简洁、口语化，聚焦答案中尚未充分展开的方面。\n"
        "3. 问题之间避免重复。\n\n"
        "输入：\n"
        "用户问题：{question}\n"
        "答案：{answer}\n"
        "检索片段：\n{chunks}\n\n"
        "输出格式：[\"追问一\", \"追问二\", ...]"
    ),
}


class AgentPromptCacheManager:
    """
    智能体提示词缓存管理器·进程内版（对应 Java AgentPromptCacheManager）

    Java 侧缓存激活智能体叠加自定义提示词后的结果于 Redis（1 小时过期）；
    本类为进程内兜底 / 测试注入用：命中直接返回、未命中返回 None、clear 后强制重载。
    Redis 版（TTL 1 小时、经 5.0 CacheManager 抽象）见 agent_resolver.RedisAgentPromptCacheManager。
    任何写操作后调用 clear_cache() 使缓存失效。
    """

    def __init__(self):
        self._store: Optional[Dict[str, str]] = None

    def get_from_cache(self) -> Optional[Dict[str, str]]:
        """返回槽位到提示词的映射；缓存不存在返回 None"""
        return self._store

    def save_to_cache(self, prompts: Dict[str, str]) -> None:
        """保存解析结果快照"""
        self._store = dict(prompts or {})

    def clear_cache(self) -> None:
        """清除缓存，下次解析强制重载"""
        self._store = None


class AgentPromptResolver(ABC):
    """
    智能体提示词解析器接口（对应 Java AgentPromptResolver）

    Java 侧优先取激活智能体的槽位、空白则回落内置智能体；面向终端用户的提示词一律从此处读取。
    Python 侧以 ABC 抽象出「槽位 → 提示词」的解析边界，真实后端（DB 叠加回落）按需实现注入。
    """

    @abstractmethod
    def resolve(self, slot: Optional[AgentPromptSlot]) -> str:
        """
        解析槽位提示词；内置智能体也没配时返回空串
        """
        ...

    @abstractmethod
    def render(self, slot: AgentPromptSlot, slots: Optional[Dict[str, str]]) -> str:
        """
        填充占位符并清理格式，语义与 PromptTemplateLoader.render 一致
        """
        ...

    @abstractmethod
    def resolve_all(self) -> Dict[str, str]:
        """
        全部槽位的最终生效内容，缺失的槽位不出现在 map 中
        """
        ...


class StaticAgentPromptResolver(AgentPromptResolver):
    """
    内存版智能体提示词解析器（对应 Java AgentPromptResolver 的加载/缓存流程）

    以注入的 dict（槽位名 → 提示词）为唯一数据源，走「缓存 → 加载 → 落缓存」三段流程，
    语义与 Java 的 resolveAll 一致。真实后端（DB 叠加回落）实现 AgentPromptResolver 后注入即可替换。

    Args:
        prompts: 槽位名 → 提示词，缺失的槽位 resolve 返回空串
        cache_manager: 解析结果缓存，默认新建进程内缓存
    """

    def __init__(
        self,
        prompts: Optional[Dict[str, str]] = None,
        cache_manager: Optional[AgentPromptCacheManager] = None,
    ):
        self._source = {k: v for k, v in (prompts or {}).items() if v is not None}
        self._cache_manager = cache_manager or AgentPromptCacheManager()

    def resolve(self, slot: Optional[AgentPromptSlot]) -> str:
        if slot is None:
            return ""
        value = self.resolve_all().get(slot.name)
        if value:
            return value
        # 未配置回落内置默认（仅对已声明默认值的槽位生效，其余仍空串）
        return DEFAULT_AGENT_PROMPTS.get(slot.name, "")

    def render(self, slot: AgentPromptSlot, slots: Optional[Dict[str, str]]) -> str:
        return PromptTemplateUtils.cleanup_prompt(
            PromptTemplateUtils.fill_slots(self.resolve(slot), slots)
        )

    def resolve_all(self) -> Dict[str, str]:
        cached = self._cache_manager.get_from_cache()
        if cached is not None:
            return cached
        resolved = dict(self._source)
        self._cache_manager.save_to_cache(resolved)
        return resolved


class RAGPromptService:
    """
    RAG Prompt 编排服务（对应 Java RAGPromptService）

    根据检索结果场景（KB / MCP / Mixed）选择模板，并构造最终发送给 LLM 的消息序列。

    Args:
        template_loader: 模板加载器，默认 PromptTemplateLoader()
        agent_prompt_resolver: 智能体提示词解析器，默认 StaticAgentPromptResolver()（未配置时返回空串）
        citation_enabled: 引用开关（对应 Java RAGConfigProperties.citationEnabled），
            关闭时不追加行内引用规则
    """

    def __init__(
        self,
        template_loader: Optional[PromptTemplateLoader] = None,
        agent_prompt_resolver: Optional[AgentPromptResolver] = None,
        citation_enabled: bool = True,
    ):
        self._template_loader = template_loader or PromptTemplateLoader()
        self._agent_prompt_resolver = agent_prompt_resolver or StaticAgentPromptResolver()
        self._citation_enabled = citation_enabled

    # ==================== 对外入口 ====================

    def build_system_prompt(self, context: PromptContext) -> str:
        """
        生成系统提示词，并对模板格式做清理（对应 Java buildSystemPrompt）

        KB 场景且引用开关打开时，把行内引用规则追加到基础模板之后。
        """
        plan = self._plan(context)
        template = (
            plan.base_template
            if plan.base_template and plan.base_template.strip()
            else self._default_template(plan.scene)
        )
        system_prompt = (
            "" if not template or not template.strip() else PromptTemplateUtils.cleanup_prompt(template)
        )
        if not context.has_kb() or not self._citation_enabled:
            return system_prompt

        citation_rules = PromptTemplateUtils.cleanup_prompt(
            self._template_loader.load(ANSWER_CITATION_RULES_PROMPT_PATH)
        )
        if not system_prompt or not system_prompt.strip():
            return citation_rules
        if not citation_rules or not citation_rules.strip():
            return system_prompt
        return system_prompt + "\n\n" + citation_rules

    def build_structured_messages(
        self,
        context: PromptContext,
        history: Optional[List[Message]],
        question: Optional[str],
        sub_questions: Optional[List[str]],
    ) -> List[Message]:
        """
        构造发送给 LLM 的完整消息列表（system + history + evidence + user）（对应 Java buildStructuredMessages）

        Args:
            context:        Prompt 构建上下文
            history:        对话历史（含摘要，摘要作为 history[0] 的 system message 紧跟系统提示词）
            question:       本次问题
            sub_questions:  子问题列表（复杂问题拆分产物）
        """
        messages: List[Message] = []

        # 1. 系统提示词
        system_prompt = self.build_system_prompt(context)
        if system_prompt and system_prompt.strip():
            messages.append(Message.system(system_prompt))

        # 2. 对话历史
        if history:
            messages.extend(history)

        # 3. 证据 + 问题（合并为一条 user message）
        evidence_body = self._build_evidence_body(context)
        user_question = self._build_user_question(question, sub_questions)
        user_content = self._merge_evidence_and_question(evidence_body, user_question)
        if user_content and user_content.strip():
            messages.append(Message.user(user_content))

        return messages

    # ==================== 场景规划 ====================

    def _plan_prompt(self, intents: Optional[List[NodeScore]], retrieved_intent_ids: Set[str]) -> PromptPlan:
        """
        从意图列表选出候选并决定基础模板（对应 Java planPrompt）

        过滤规则：无归属过滤（retrievedIntentIds 非空时只保留命中的意图），按意图 ID 去重保序；
        恰好 1 个候选且自带非空 prompt_template 时采用该模板，否则返回 None 走默认模板。
        """
        eligible_by_id: Dict[str, NodeScore] = {}
        for intent in intents or []:
            if intent is None or intent.node is None:
                continue
            intent_id = intent.node.id
            if retrieved_intent_ids and intent_id not in retrieved_intent_ids:
                continue
            eligible_by_id.setdefault(intent_id, intent)

        eligible = list(eligible_by_id.values())
        if not eligible:
            return PromptPlan(retained_intents=[], base_template=None)
        if len(eligible) == 1:
            tpl = (eligible[0].node.prompt_template or "").strip()
            if tpl:
                return PromptPlan(retained_intents=eligible, base_template=tpl)
        return PromptPlan(retained_intents=eligible, base_template=None)

    def _plan(self, context: PromptContext) -> PromptBuildPlan:
        """按 MCP / KB 上下文的有无决定场景（对应 Java plan）"""
        if context.has_mcp() and not context.has_kb():
            return self._plan_mcp_only(context)
        if not context.has_mcp() and context.has_kb():
            return self._plan_kb_only(context)
        if context.has_mcp() and context.has_kb():
            return self._plan_mixed(context)
        raise ValueError("PromptContext requires MCP or KB context.")

    def _plan_kb_only(self, context: PromptContext) -> PromptBuildPlan:
        plan = self._plan_prompt(context.kb_intents, context.retrieved_intent_ids)
        return PromptBuildPlan(
            scene=PromptScene.KB_ONLY,
            base_template=plan.base_template,
            mcp_context=context.mcp_context,
            kb_context=context.kb_context,
            question=context.question,
        )

    def _plan_mcp_only(self, context: PromptContext) -> PromptBuildPlan:
        base_template = None
        intents = context.mcp_intents or []
        if len(intents) == 1 and intents[0].node is not None:
            tpl = (intents[0].node.prompt_template or "").strip()
            if tpl:
                base_template = tpl
        return PromptBuildPlan(
            scene=PromptScene.MCP_ONLY,
            base_template=base_template,
            mcp_context=context.mcp_context,
            kb_context=context.kb_context,
            question=context.question,
        )

    def _plan_mixed(self, context: PromptContext) -> PromptBuildPlan:
        return PromptBuildPlan(
            scene=PromptScene.MIXED,
            base_template=None,
            mcp_context=context.mcp_context,
            kb_context=context.kb_context,
            question=context.question,
        )

    def _default_template(self, scene: PromptScene) -> str:
        """场景对应的默认槽位模板（对应 Java defaultTemplate）"""
        if scene == PromptScene.KB_ONLY:
            return self._agent_prompt_resolver.resolve(AgentPromptSlot.KB_ANSWER)
        if scene == PromptScene.MCP_ONLY:
            return self._agent_prompt_resolver.resolve(AgentPromptSlot.MCP_ANSWER)
        if scene == PromptScene.MIXED:
            return self._agent_prompt_resolver.resolve(AgentPromptSlot.MIXED_ANSWER)
        return ""

    # ==================== 证据与问题渲染 ====================

    def _build_user_question(
        self, question: Optional[str], sub_questions: Optional[List[str]]
    ) -> str:
        """多子问题走 multi-questions 段，否则单问题段（对应 Java buildUserQuestion）"""
        if sub_questions and len(sub_questions) > 1:
            numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sub_questions))
            return self._render_section("multi-questions", {"questions": numbered})
        if not question or not question.strip():
            return ""
        return self._render_section("single-question", {"question": question})

    def _merge_evidence_and_question(self, evidence_body: str, question: str) -> str:
        """证据与问题合并为一条 user content（对应 Java mergeEvidenceAndQuestion）"""
        if not evidence_body or not evidence_body.strip():
            return question
        if not question or not question.strip():
            return evidence_body
        return evidence_body + "\n\n" + question

    def _build_evidence_body(self, context: PromptContext) -> str:
        """将 MCP 和 KB 证据合并为一个文本块，各自有值时用对应 section 渲染（对应 Java buildEvidenceBody）"""
        parts: List[str] = []
        if context.mcp_context and context.mcp_context.strip():
            parts.append(self._render_section("mcp-evidence", {"body": context.mcp_context.strip()}))
        if context.kb_context and context.kb_context.strip():
            parts.append(self._render_section("kb-evidence", {"body": context.kb_context.strip()}))
        return "\n\n".join(parts).strip()

    def _render_section(self, section: str, slots: Dict[str, str]) -> str:
        return self._template_loader.render_section(CONTEXT_FORMAT_PATH, section, slots)
