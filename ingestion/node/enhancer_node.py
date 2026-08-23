# -*- coding: utf-8 -*-
"""
ingestion.node.enhancer_node - 文本增强节点（对应 Java EnhancerNode）

对整篇文本调用 LLM 做增强：上下文增强 / 关键词提取 / 问题生成 / 元数据提取。
    - 无任务配置 → 直接 ok
    - 默认系统提示词走 EnhancerPromptManager；显式 systemPrompt 优先
    - 用户提示词模板缺省直接用输入文本；模板变量：text/content/mimeType/taskId/pipelineId
    - 结果按类型写回上下文（enhanced_text / keywords / questions / metadata）

对应 ragent 源码：
    - ingestion/node/EnhancerNode
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.llm.chat import LLMService
from core.llm.enums import Tier
from core.llm.schema import ChatRequest, Message
from ingestion.domain.context import IngestionContext
from ingestion.domain.enums import EnhanceType, IngestionNodeType
from ingestion.domain.pipeline import NodeConfig
from ingestion.domain.result import NodeResult
from ingestion.domain.settings import EnhancerSettings
from ingestion.node.base import IngestionNode
from ingestion.prompt.enhancer_prompt_manager import system_prompt as default_system_prompt
from ingestion.util.json_response_parser import parse_object, parse_string_list
from ingestion.util.prompt_template_renderer import render as render_template


class EnhancerNode(IngestionNode):
    """文本增强节点（对齐 Java EnhancerNode）"""

    def __init__(self, llm_service: LLMService):
        self._llm = llm_service

    def get_node_type(self) -> str:
        return IngestionNodeType.ENHANCER.value

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        settings = _parse_settings(config.settings)
        if not settings.tasks:
            return NodeResult.ok("未配置增强任务")
        if context.metadata is None:
            context.metadata = {}

        for task in settings.tasks:
            if task is None or task.type is None:
                continue
            type_ = task.type
            input_text = _resolve_input_text(context, type_)
            if not input_text:
                continue
            system_prompt = task.system_prompt if task.system_prompt else default_system_prompt(type_)
            user_prompt = _build_user_prompt(task.user_prompt_template, input_text, context)

            request = ChatRequest(messages=[
                Message.system(system_prompt or ""),
                Message.user(user_prompt),
            ])
            response = await self._llm.chat(
                request, tier=Tier.FAST, preferred_model_id=settings.model_id
            )
            _apply_task_result(context, type_, response)

        return NodeResult.ok("增强完成")


def _parse_settings(raw: Optional[dict]) -> EnhancerSettings:
    if not raw:
        return EnhancerSettings()
    tasks = []
    for item in raw.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        tasks.append(EnhancerSettings.EnhanceTask(
            type=EnhanceType.from_value(item.get("type")),
            system_prompt=item.get("systemPrompt"),
            user_prompt_template=item.get("userPromptTemplate"),
        ))
    return EnhancerSettings(model_id=raw.get("modelId"), tasks=tasks)


def _resolve_input_text(context: IngestionContext, type_: EnhanceType) -> Optional[str]:
    if type_ is EnhanceType.CONTEXT_ENHANCE:
        return context.raw_text
    return context.enhanced_text if context.enhanced_text else context.raw_text


def _build_user_prompt(template: Optional[str], input_text: str,
                       context: IngestionContext) -> str:
    if not template:
        return input_text
    variables: Dict[str, Any] = {
        "text": input_text,
        "content": input_text,
        "mimeType": context.mime_type,
        "taskId": context.task_id,
        "pipelineId": context.pipeline_id,
    }
    return render_template(template, variables)


def _apply_task_result(context: IngestionContext, type_: EnhanceType, response: str) -> None:
    if type_ is EnhanceType.CONTEXT_ENHANCE:
        context.enhanced_text = response.strip() if response else response
    elif type_ is EnhanceType.KEYWORDS:
        context.keywords = parse_string_list(response)
    elif type_ is EnhanceType.QUESTIONS:
        context.questions = parse_string_list(response)
    elif type_ is EnhanceType.METADATA:
        context.metadata.update(parse_object(response))
