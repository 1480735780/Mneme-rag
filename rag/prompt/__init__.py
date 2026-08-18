"""
rag.prompt - 提示词编排

    - formatter：上下文格式化（ContextFormatter + DefaultContextFormatter）
      与模板基建（PromptTemplateLoader + PromptTemplateUtils + templates/*.st）
    - builder：Prompt 场景选择与装配（PromptScene / PromptContext / RAGPromptService / AgentPrompt*）
    - agent_resolver：智能体提示词解析器的 DB 数据源实现 + Redis 版缓存管理器
      （DatabaseAgentPromptResolver 叠加回落 + RedisAgentPromptCacheManager TTL 1h）

对应 ragent 源码：
    - rag/core/prompt/ContextFormatter + DefaultContextFormatter
    - rag/core/prompt/PromptTemplateLoader + PromptTemplateUtils
    - rag/core/prompt/PromptScene + PromptContext + RAGPromptService + AgentPrompt*
"""
from rag.prompt.agent_resolver import (
    AGENT_PROMPT_CACHE_KEY,
    AGENT_PROMPT_CACHE_TTL_SECONDS,
    AGENT_PROFILE_TABLE,
    AGENT_PROMPT_TABLE,
    DatabaseAgentPromptResolver,
    RedisAgentPromptCacheManager,
)
from rag.prompt.builder import (
    ANSWER_CITATION_RULES_PROMPT_PATH,
    AgentPromptCacheManager,
    AgentPromptResolver,
    AgentPromptSlot,
    OrchestrationMode,
    PromptBuildPlan,
    PromptContext,
    PromptPlan,
    PromptScene,
    RAGPromptService,
    SlotGroup,
    StaticAgentPromptResolver,
)
from rag.prompt.formatter import (
    CONTEXT_FORMAT_PATH,
    ContextFormatter,
    DefaultContextFormatter,
    PromptTemplateError,
    PromptTemplateLoader,
    PromptTemplateUtils,
    ToolResult,
)

__all__ = [
    "AGENT_PROMPT_CACHE_KEY",
    "AGENT_PROMPT_CACHE_TTL_SECONDS",
    "AGENT_PROMPT_TABLE",
    "AGENT_PROFILE_TABLE",
    "ANSWER_CITATION_RULES_PROMPT_PATH",
    "CONTEXT_FORMAT_PATH",
    "AgentPromptCacheManager",
    "AgentPromptResolver",
    "AgentPromptSlot",
    "ContextFormatter",
    "DatabaseAgentPromptResolver",
    "DefaultContextFormatter",
    "OrchestrationMode",
    "PromptBuildPlan",
    "PromptContext",
    "PromptPlan",
    "PromptScene",
    "PromptTemplateError",
    "PromptTemplateLoader",
    "PromptTemplateUtils",
    "RAGPromptService",
    "RedisAgentPromptCacheManager",
    "SlotGroup",
    "StaticAgentPromptResolver",
    "ToolResult",
]
