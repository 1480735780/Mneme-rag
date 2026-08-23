# -*- coding: utf-8 -*-
"""
ingestion.node.enricher_node - 分块富集节点（对应 Java EnricherNode）

对每个分块调用 LLM 做信息提取/补充（关键词 / 摘要 / 元数据）。
    - 块不可变：加工产物收集到扩展位，最后整块替换（with_extras），不原地改自由 Map
    - attachDocumentMetadata 缺省 True：把文档级元数据并入每块 extras
    - 默认系统提示词走 EnricherPromptManager；显式 systemPrompt 优先

对应 ragent 源码：
    - ingestion/node/EnricherNode
"""
from __future__ import annotations

from typing import Dict, List, Optional

from core.llm.chat import LLMService
from core.llm.enums import Tier
from core.llm.schema import ChatRequest, EmbeddedChunk, Message
from ingestion.domain.context import IngestionContext
from ingestion.domain.enums import ChunkEnrichType, IngestionNodeType
from ingestion.domain.pipeline import NodeConfig
from ingestion.domain.result import NodeResult
from ingestion.domain.settings import EnricherSettings
from ingestion.node.base import IngestionNode
from ingestion.prompt.enricher_prompt_manager import system_prompt as default_system_prompt
from ingestion.util.json_response_parser import parse_object, parse_string_list
from ingestion.util.prompt_template_renderer import render as render_template


class EnricherNode(IngestionNode):
    """分块富集节点（对齐 Java EnricherNode）"""

    def __init__(self, llm_service: LLMService):
        self._llm = llm_service

    def get_node_type(self) -> str:
        return IngestionNodeType.ENRICHER.value

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        chunks = context.chunks or []
        if not chunks:
            return NodeResult.ok("No chunks to enrich")
        settings = _parse_settings(config.settings)
        if not settings.tasks:
            return NodeResult.ok("No enricher tasks configured")

        attach_metadata = settings.attach_document_metadata is None or settings.attach_document_metadata
        enriched: List[EmbeddedChunk] = []
        for chunk in chunks:
            if chunk is None:
                continue
            if not chunk.content:
                enriched.append(chunk)
                continue
            extras: Dict[str, object] = {}
            if attach_metadata and context.metadata:
                extras.update(context.metadata)
            for task in settings.tasks:
                if task is None or task.type is None:
                    continue
                type_ = task.type
                system_prompt = task.system_prompt if task.system_prompt else default_system_prompt(type_)
                user_prompt = _build_user_prompt(task.user_prompt_template, chunk, context)
                request = ChatRequest(messages=[
                    Message.system(system_prompt or ""),
                    Message.user(user_prompt),
                ])
                response = await self._llm.chat(
                    request, tier=Tier.FAST, preferred_model_id=settings.model_id
                )
                _apply_result(extras, type_, response)
            if extras:
                enriched.append(EmbeddedChunk(
                    chunk=chunk.chunk.with_metadata(chunk.metadata.with_extras(extras)),
                    embedding=chunk.embedding,
                ))
            else:
                enriched.append(chunk)
        context.chunks = enriched
        return NodeResult.ok("Enricher completed")


def _parse_settings(raw: Optional[dict]) -> EnricherSettings:
    if not raw:
        return EnricherSettings()
    tasks = []
    for item in raw.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        tasks.append(EnricherSettings.ChunkEnrichTask(
            type=ChunkEnrichType.from_value(item.get("type")),
            system_prompt=item.get("systemPrompt"),
            user_prompt_template=item.get("userPromptTemplate"),
        ))
    return EnricherSettings(
        model_id=raw.get("modelId"),
        attach_document_metadata=raw.get("attachDocumentMetadata"),
        tasks=tasks,
    )


def _build_user_prompt(template: Optional[str], chunk: EmbeddedChunk,
                       context: IngestionContext) -> str:
    input_text = chunk.content
    if not template:
        return input_text
    variables: Dict[str, object] = {
        "text": input_text,
        "content": input_text,
        "chunkIndex": chunk.index,
        "taskId": context.task_id,
        "pipelineId": context.pipeline_id,
    }
    return render_template(template, variables)


def _apply_result(extras: Dict[str, object], type_: ChunkEnrichType, response: str) -> None:
    if type_ is ChunkEnrichType.KEYWORDS:
        extras["keywords"] = parse_string_list(response)
    elif type_ is ChunkEnrichType.SUMMARY:
        extras["summary"] = response.strip() if response else response
    elif type_ is ChunkEnrichType.METADATA:
        extras.update(parse_object(response))
