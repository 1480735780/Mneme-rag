# -*- coding: utf-8 -*-
"""
core.llm.sse_parser - OpenAI 兼容协议 SSE 解析器（对应 ragent 的 OpenAIStyleSseParser）

解析流式响应中的单行 SSE 事件：剥离 "data:" 前缀、识别 "[DONE]" 结束标记、
从 choices[0] 的 delta/message 中提取增量 content 与可选的 reasoning_content，
并依据 finish_reason 判断本次流是否已结束。

架构对应关系：
    Ragent (Java)                          Mneme-rag (Python)
    ──────────────────────────────────────────────────
    infra/chat/OpenAIStyleSseParser.java --> core/llm/sse_parser.py

设计说明：
    - 纯函数式解析器，无状态、无外部依赖，可独立单测；
    - 单行输入（流式循环每读一行调用一次），解析失败返回空事件而非抛错
      （对齐 Java：解析异常由调用方捕获降级，不影响后续行）；
    - reasoning_content 仅在 reasoning_enabled 时提取（对应 Java 的
      reasoningEnabled 开关，由请求的 thinking 标志决定）。
"""

import json
from dataclasses import dataclass
from typing import Optional


DATA_PREFIX = "data:"
DONE_MARKER = "[DONE]"


@dataclass(frozen=True)
class ParsedEvent:
    """
    单行 SSE 解析结果（对应 Java 的 ParsedEvent record）。

    Attributes:
        content: 增量内容片段，无则为 None。
        reasoning: 增量思考内容片段，无或未开启解析则为 None。
        completed: 是否流已结束（[DONE] 或 finish_reason 出现）。
    """

    content: Optional[str] = None
    reasoning: Optional[str] = None
    completed: bool = False

    def has_content(self) -> bool:
        """是否有可回调的内容（非空）。"""
        return self.content is not None and self.content.strip() != ""

    def has_reasoning(self) -> bool:
        """是否有可回调的思考内容（非空）。"""
        return self.reasoning is not None and len(self.reasoning) > 0


class OpenAIStyleSseParser:
    """
    OpenAI 兼容协议 SSE 解析器（对应 Java 的 OpenAIStyleSseParser）。

    使用方式：
        async for line in response.aiter_lines():
            event = OpenAIStyleSseParser.parse_line(line, reasoning_enabled=True)
            if event.has_reasoning():
                await callback.on_thinking(event.reasoning)
            if event.has_content():
                await callback.on_content(event.content)
            if event.completed:
                break
    """

    @staticmethod
    def parse_line(
        line: Optional[str],
        reasoning_enabled: bool = False,
    ) -> ParsedEvent:
        """
        解析一行 SSE 文本（对应 Java 的 parseLine）。

        处理步骤（与 Java 逐行对齐）：
            1. 空行/空串 → 空事件；
            2. 剥离 "data:" 前缀；
            3. "[DONE]"（忽略大小写）→ 完成事件；
            4. JSON 解析 → choices[0] 中提取 content / reasoning_content，
               出现 finish_reason 视为完成。

        Args:
            line: SSE 原始行，如 'data: {"choices":[{"delta":{"content":"你"}}]}'。
            reasoning_enabled: 是否解析 reasoning_content（思考请求时开启）。

        Returns:
            ParsedEvent: 解析结果；任何解析失败都返回空事件而非抛错。
        """
        if line is None or line.strip() == "":
            return ParsedEvent()

        payload = line.strip()
        if payload.startswith(DATA_PREFIX):
            payload = payload[len(DATA_PREFIX):].strip()

        if payload.upper() == DONE_MARKER:
            return ParsedEvent(completed=True)

        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return ParsedEvent()

        if not isinstance(obj, dict):
            return ParsedEvent()

        choices = obj.get("choices")
        if not choices:
            return ParsedEvent()

        choice0 = choices[0]
        if not isinstance(choice0, dict):
            return ParsedEvent()

        content = OpenAIStyleSseParser._extract_text(choice0, "content")
        reasoning = (
            OpenAIStyleSseParser._extract_text(choice0, "reasoning_content")
            if reasoning_enabled else None
        )
        completed = choice0.get("finish_reason") is not None

        return ParsedEvent(content=content, reasoning=reasoning, completed=completed)

    @staticmethod
    def _extract_text(choice: dict, field_name: str) -> Optional[str]:
        """
        从 choice 中提取字段文本（对应 Java 的 extractText）。

        兼容两种结构（部分供应商用 message 而非 delta）：
            1. choices[0].delta.<field>
            2. choices[0].message.<field>
        """
        delta = choice.get("delta")
        if isinstance(delta, dict):
            value = delta.get(field_name)
            if value is not None and not isinstance(value, bool):
                return str(value)

        message = choice.get("message")
        if isinstance(message, dict):
            value = message.get(field_name)
            if value is not None and not isinstance(value, bool):
                return str(value)

        return None
