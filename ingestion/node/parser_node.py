# -*- coding: utf-8 -*-
"""
ingestion.node.parser_node - 文档解析节点（对应 Java ParserNode）

把输入字节流解析为结构化 Block 树：
    - 校验 raw_bytes 非空、MIME（缺省按文件名探测）、规则白名单（未配置放行全部）
    - 按 (MIME × 默认档) 查 ParserRegistry，不匹配显式报错不静默兜底
    - options 注入 sourceFile / documentId（资产归属）
    - blocks 渲染纯文本 → raw_text；StructuredDocument 落 context.document

对应 ragent 源码：
    - ingestion/node/ParserNode
"""
from __future__ import annotations

from typing import Dict, List, Optional

from common.exception.business import ClientException
from ingestion.domain.context import IngestionContext, StructuredDocument
from ingestion.domain.enums import IngestionNodeType
from ingestion.domain.pipeline import NodeConfig
from ingestion.domain.result import NodeResult
from ingestion.domain.settings import ParserSettings
from ingestion.node.base import IngestionNode
from rag.ingestion.kernel import MimeTypeDetector
from rag.ingestion.parser.registry import ParseProfile, ParserRegistry
from rag.ingestion.parser.renderer import BlockTextRenderer


class ParserNode(IngestionNode):
    """文档解析节点（对齐 Java ParserNode）"""

    def __init__(self, parser_registry: ParserRegistry):
        self._registry = parser_registry

    def get_node_type(self) -> str:
        return IngestionNodeType.PARSER.value

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        if not context.raw_bytes:
            return NodeResult.fail(ClientException("解析器缺少原始字节"))

        mime_type = context.mime_type
        if not mime_type:
            file_name = context.source.file_name if context.source is not None else None
            mime_type = MimeTypeDetector.detect(context.raw_bytes, file_name)
            context.mime_type = mime_type

        settings = _parse_settings(config.settings)
        file_name = context.source.file_name if context.source is not None else None

        try:
            _validate_mime_type(settings, mime_type, file_name)
        except ClientException as exc:
            return NodeResult.fail(exc)

        rule = _match_rule(settings, mime_type, file_name)
        parser = self._registry.find(mime_type, ParseProfile.default_profile())
        if parser is None:
            return NodeResult.fail(ClientException(
                f"未找到 MIME [{mime_type}] 对应的解析器,fileName={file_name}"
            ))

        options: Dict[str, object] = dict(rule.options) if rule is not None and rule.options else {}
        if file_name and "sourceFile" not in options:
            options["sourceFile"] = file_name
        if context.task_id and "documentId" not in options:
            options["documentId"] = context.task_id

        parsed = parser.parse_structured(context.raw_bytes, mime_type, options)
        blocks = parsed.blocks if parsed.blocks is not None else []
        rendered_text = BlockTextRenderer.render(blocks)
        context.raw_text = rendered_text
        context.document = StructuredDocument(
            text=rendered_text, blocks=blocks, metadata=parsed.metadata
        )
        return NodeResult.ok(
            f"解析器={parser.parser_type}, blocks={len(blocks)}, 文本长度={len(rendered_text)}"
        )


def _parse_settings(raw: Optional[dict]) -> ParserSettings:
    """config.settings dict → ParserSettings（对齐 Java parseSettings 的 convertValue）"""
    if not raw:
        return ParserSettings()
    rules = []
    for item in raw.get("rules") or []:
        if not isinstance(item, dict):
            continue
        rules.append(ParserSettings.ParserRule(
            mime_type=item.get("mimeType"),
            options=item.get("options"),
        ))
    return ParserSettings(rules=rules)


def _validate_mime_type(settings: ParserSettings, mime_type: Optional[str],
                        file_name: Optional[str]) -> None:
    """规则白名单校验：未配置规则放行全部（对齐 Java validateMimeType）"""
    if not settings.rules:
        return
    resolved = _resolve_type(mime_type, file_name)
    has_match = False
    allowed: List[str] = []
    for rule in settings.rules:
        if not rule or not rule.mime_type:
            continue
        configured = _normalize_type(rule.mime_type)
        if not configured:
            continue
        allowed.append(configured)
        if configured == "ALL" or configured == resolved:
            has_match = True
            break
    if not has_match:
        distinct = list(dict.fromkeys(allowed))
        raise ClientException(
            f"文件类型不符合要求。当前文件类型: {resolved}，允许的类型: {', '.join(distinct)}"
        )


def _match_rule(settings: ParserSettings, mime_type: Optional[str],
                file_name: Optional[str]) -> Optional[ParserSettings.ParserRule]:
    """返回命中的规则（含 options）；未配置规则返回 None（对齐 Java matchRule）"""
    if not settings.rules:
        return None
    resolved = _resolve_type(mime_type, file_name)
    for rule in settings.rules:
        if not rule or not rule.mime_type:
            continue
        configured = _normalize_type(rule.mime_type)
        if not configured:
            continue
        if configured == "ALL" or configured == resolved:
            return rule
    return None


def _resolve_type(mime_type: Optional[str], file_name: Optional[str]) -> str:
    """解析文件类型（先按扩展名，再按 MIME 关键字，对齐 Java resolveType）"""
    by_name = _resolve_type_by_name(file_name)
    if by_name:
        return by_name
    if not mime_type:
        return "UNKNOWN"
    lower = mime_type.strip().lower()
    if "pdf" in lower:
        return "PDF"
    if "markdown" in lower:
        return "MARKDOWN"
    if "word" in lower or "msword" in lower or "wordprocessingml" in lower:
        return "WORD"
    if "excel" in lower or "spreadsheetml" in lower:
        return "EXCEL"
    if "powerpoint" in lower or "presentation" in lower:
        return "PPT"
    if lower.startswith("image/"):
        return "IMAGE"
    if lower.startswith("text/"):
        return "TEXT"
    return "UNKNOWN"


def _resolve_type_by_name(file_name: Optional[str]) -> Optional[str]:
    if not file_name:
        return None
    lower = file_name.lower()
    if lower.endswith(".pdf"):
        return "PDF"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return "MARKDOWN"
    if lower.endswith(".doc") or lower.endswith(".docx"):
        return "WORD"
    if lower.endswith(".xls") or lower.endswith(".xlsx"):
        return "EXCEL"
    if lower.endswith(".ppt") or lower.endswith(".pptx"):
        return "PPT"
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
        return "IMAGE"
    if lower.endswith(".txt"):
        return "TEXT"
    return None


def _normalize_type(raw: str) -> Optional[str]:
    """配置类型归一化（* / ALL / DEFAULT → ALL，别名收敛，对齐 Java normalizeType）"""
    value = raw.strip().upper()
    if value in ("*", "ALL", "DEFAULT"):
        return "ALL"
    if value in ("MD", "MARKDOWN"):
        return "MARKDOWN"
    if value in ("DOC", "DOCX", "WORD"):
        return "WORD"
    if value in ("XLS", "XLSX", "EXCEL"):
        return "EXCEL"
    if value in ("PPT", "PPTX", "POWERPOINT"):
        return "PPT"
    if value in ("TXT", "TEXT"):
        return "TEXT"
    if value in ("PNG", "JPG", "JPEG", "GIF", "BMP", "WEBP", "IMAGE", "IMG"):
        return "IMAGE"
    if value == "PDF":
        return "PDF"
    return value
