"""
rag.prompt - 提示词编排

    - formatter：上下文格式化（ContextFormatter + DefaultContextFormatter）
      与模板基建（PromptTemplateLoader + PromptTemplateUtils + templates/*.st）
    - builder：Prompt 场景选择与装配（PromptScene / PromptContext / RAGPromptService / AgentPrompt*）

对应 ragent 源码：
    - rag/core/prompt/ContextFormatter + DefaultContextFormatter
    - rag/core/prompt/PromptTemplateLoader + PromptTemplateUtils
    - rag/core/prompt/PromptScene + PromptContext + RAGPromptService + AgentPrompt*
"""
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
    "ANSWER_CITATION_RULES_PROMPT_PATH",
    "CONTEXT_FORMAT_PATH",
    "AgentPromptCacheManager",
    "AgentPromptResolver",
    "AgentPromptSlot",
    "ContextFormatter",
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
    "SlotGroup",
    "StaticAgentPromptResolver",
    "ToolResult",
]
