# -*- coding: utf-8 -*-
"""
rag.memory.compression.deterministic - 摘要前的确定性压缩（V2.1.1 / P-03）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 3 节

P-03 决策：流水线顺序必须是
    Cut → **Deterministic** → Split Check → Metadata → Summary
而不是 V2.1 原稿的
    Cut → Split → **Deterministic** → ...

理由：Deterministic 会把 tool result / 长消息 / 重复内容 rule-based 削掉，
很多原本"超预算需要 Split Turn"的 turn 经过 deterministic 后就不超了。
反序会导致 Split Turn 与 Deterministic 双重截断，信息损失被放大。

本模块实现 Deterministic 部分（**Split Turn 依赖 Phase 2 Token-aware Cut Point，
待 Phase 2 就绪时接入到 deterministic_compress 之后**，接口已按此位置预留）。

V2.1.1 阶段实现的规则（覆盖 mneme-rag 当前数据形态）：
    1. 长消息裁剪：单条 message.content 超阈值时，保留 head + marker + tail，
       中段替换为截断标记（对齐 pi-mono serializeConversation 里 tool result
       截 2000 字的做法，本项目里没有 tool result 直接落 content，但用户可能
       粘贴长文本； assistant 也可能因引用 KB chunk 内容而膨胀）
    2. 重复内容去重：同一 message.content 多次出现（例如用户复述同一段），
       保留首次出现，后续替换为"[重复内容已折叠]"标记

Phase 6 完整版会补：
    3. KB chunk 头尾裁剪（当前 retrieved_chunks 只落 grounding 用不影响 message）
    4. Low-value metadata 剥离（trace_id / request_id / debug_id / provider_metadata）
    5. MCP Tool Result 截断（当前 MCP 结果不进 t_message.content）

配置默认值参考 pi-mono：head 1500 + tail 500 + 阈值 2000 触发。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import List, Set

from core.llm.schema import Message

logger = logging.getLogger(__name__)

# 长消息触发阈值：超过此长度的 message.content 会被裁剪
_DEFAULT_LONG_MESSAGE_THRESHOLD = 3000
# head 保留字符数
_DEFAULT_HEAD_CHARS = 1500
# tail 保留字符数
_DEFAULT_TAIL_CHARS = 500
# 截断标记模板（LLM 能理解"这里被规则压缩了"而不是幻觉补齐）
_TRUNCATION_MARKER_TEMPLATE = "\n\n... [中间 {dropped} 字符已被规则压缩; 若需要完整内容请重新检索] ...\n\n"
# 重复内容折叠标记
_DUPLICATE_MARKER = "[与前一条消息内容重复，已折叠]"


@dataclass
class DeterministicCompressConfig:
    """
    确定性压缩配置

    Attributes:
        long_message_threshold: 触发裁剪的最小字符数
        head_chars: 触发裁剪时保留头部字符数
        tail_chars: 触发裁剪时保留尾部字符数
        dedup_enabled: 是否启用重复内容折叠
    """

    long_message_threshold: int = _DEFAULT_LONG_MESSAGE_THRESHOLD
    head_chars: int = _DEFAULT_HEAD_CHARS
    tail_chars: int = _DEFAULT_TAIL_CHARS
    dedup_enabled: bool = True

    def __post_init__(self) -> None:
        if self.head_chars < 0 or self.tail_chars < 0:
            raise ValueError("head_chars / tail_chars 不能为负")
        if self.long_message_threshold <= self.head_chars + self.tail_chars:
            # 阈值小于保留总长时截断无意义，退化为不启用（不 raise，日志提示即可）
            logger.debug(
                "long_message_threshold(%s) <= head_chars+tail_chars(%s), "
                "长消息裁剪将不生效",
                self.long_message_threshold,
                self.head_chars + self.tail_chars,
            )


def deterministic_compress_messages(
    messages: List[Message],
    config: DeterministicCompressConfig,
) -> List[Message]:
    """
    对喂给 LLM 摘要的消息列表做**规则先行、LLM 后置**的确定性压缩。

    处理顺序：
        1. 逐条应用长消息裁剪（_truncate_long_message）
        2. 全列表做重复内容折叠（_dedup_messages）

    Args:
        messages: 待压缩消息列表（User/Assistant 混合）
        config: 阈值与开关

    Returns:
        List[Message]: 压缩后的新列表（不修改入参，Message 是不可变语义的 dataclass）
    """
    if not messages:
        return []

    truncated = [_truncate_long_message(m, config) for m in messages]
    if not config.dedup_enabled:
        return truncated
    return _dedup_messages(truncated)


# ==================== Rule 1: 长消息裁剪 ====================

def _truncate_long_message(
    message: Message,
    config: DeterministicCompressConfig,
) -> Message:
    content = message.content or ""
    if len(content) <= config.long_message_threshold:
        return message
    if len(content) <= config.head_chars + config.tail_chars:
        # 阈值调小了但保留段仍比全文长，别做劣化操作
        return message

    head = content[: config.head_chars]
    tail = content[-config.tail_chars :]
    dropped = len(content) - config.head_chars - config.tail_chars
    marker = _TRUNCATION_MARKER_TEMPLATE.format(dropped=dropped)
    new_content = head + marker + tail

    # Message 是 dataclass，直接构造同 role / thinking_content 的新实例，
    # 保留其它字段（sources / retrieved_chunks 等不影响摘要文本流的元数据）
    return Message(
        role=message.role,
        content=new_content,
        thinking_content=message.thinking_content,
        thinking_duration=message.thinking_duration,
        sources=message.sources,
        retrieved_chunks=message.retrieved_chunks,
        reply_to_message_id=message.reply_to_message_id,
        message_status=message.message_status,
    )


# ==================== Rule 2: 重复内容折叠 ====================

def _dedup_messages(messages: List[Message]) -> List[Message]:
    """
    按 (role, content_hash) 去重。同一条内容第二次及以后出现替换为折叠标记。

    为什么带 role：同一句话 user 与 assistant 都说过不算"重复"（语义不同）。
    为什么用 hash 不用直接字符串比较：长字符串比较 O(n)，hash 是 O(1)。
    """
    seen: Set[tuple] = set()
    result: List[Message] = []
    for msg in messages:
        content = msg.content or ""
        if not content.strip():
            result.append(msg)
            continue
        key = (msg.role.value, hashlib.sha256(content.encode("utf-8")).hexdigest())
        if key in seen:
            result.append(_replace_content(msg, _DUPLICATE_MARKER))
            continue
        seen.add(key)
        result.append(msg)
    return result


def _replace_content(message: Message, new_content: str) -> Message:
    return Message(
        role=message.role,
        content=new_content,
        thinking_content=message.thinking_content,
        thinking_duration=message.thinking_duration,
        sources=message.sources,
        retrieved_chunks=message.retrieved_chunks,
        reply_to_message_id=message.reply_to_message_id,
        message_status=message.message_status,
    )


__all__ = [
    "DeterministicCompressConfig",
    "deterministic_compress_messages",
]
