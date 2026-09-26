# -*- coding: utf-8 -*-
"""
V2.1.1 P-03 单元测试：Deterministic Compression（长消息裁剪 + 重复折叠）

覆盖：
    - 长消息裁剪：阈值触发、head/tail 保留、marker 内含被丢字符数、未超阈值原样返回
    - 短消息防御：head+tail > total 时不劣化
    - 重复内容折叠：同 role + 同 content 二次出现替换为标记，不同 role 独立算
    - 空/None content 不参与去重
    - 元数据（sources / retrieved_chunks / thinking_content / reply_to_message_id）保留
    - 配置禁用 dedup → 只做裁剪
"""
from __future__ import annotations

from core.llm.schema import Message, Role
from rag.memory.compression.deterministic import (
    DeterministicCompressConfig,
    _DUPLICATE_MARKER,
    deterministic_compress_messages,
)


# ==================== 长消息裁剪 ====================

def test_short_message_unchanged():
    msg = Message.user("x" * 100)
    out = deterministic_compress_messages(
        [msg], DeterministicCompressConfig(long_message_threshold=200, head_chars=50, tail_chars=30)
    )
    assert len(out) == 1
    assert out[0].content == "x" * 100


def test_long_message_gets_truncated():
    content = "A" * 2000 + "MIDDLE_MARKER_CONTENT" + "Z" * 2000
    msg = Message.user(content)
    out = deterministic_compress_messages(
        [msg],
        DeterministicCompressConfig(
            long_message_threshold=1000, head_chars=100, tail_chars=100, dedup_enabled=False
        ),
    )
    result = out[0].content
    # head 保留
    assert result.startswith("A" * 100)
    # tail 保留
    assert result.endswith("Z" * 100)
    # 中段被替换（原文里的 MIDDLE_MARKER_CONTENT 不再出现）
    assert "MIDDLE_MARKER_CONTENT" not in result
    # marker 提示被丢字符数
    assert "已被规则压缩" in result


def test_head_plus_tail_larger_than_content_no_op():
    """阈值调太小但保留段太大 → 不做劣化"""
    content = "short"
    msg = Message.user(content)
    out = deterministic_compress_messages(
        [msg],
        DeterministicCompressConfig(long_message_threshold=1, head_chars=100, tail_chars=100),
    )
    assert out[0].content == content


def test_message_metadata_preserved_after_truncation():
    long_content = "y" * 5000
    msg = Message(
        role=Role.ASSISTANT,
        content=long_content,
        thinking_content="reasoning here",
        thinking_duration=3,
        reply_to_message_id="msg-42",
    )
    out = deterministic_compress_messages(
        [msg],
        DeterministicCompressConfig(long_message_threshold=1000, head_chars=200, tail_chars=200, dedup_enabled=False),
    )
    assert out[0].role == Role.ASSISTANT
    assert out[0].thinking_content == "reasoning here"
    assert out[0].thinking_duration == 3
    assert out[0].reply_to_message_id == "msg-42"
    assert len(out[0].content) < len(long_content)


# ==================== 重复内容折叠 ====================

def test_duplicate_content_collapse():
    same = "完全一样的内容"
    msgs = [Message.user(same), Message.assistant("回"), Message.user(same)]
    out = deterministic_compress_messages(
        msgs, DeterministicCompressConfig(long_message_threshold=10000, dedup_enabled=True)
    )
    assert out[0].content == same
    assert out[1].content == "回"
    assert out[2].content == _DUPLICATE_MARKER


def test_different_role_same_text_not_deduped():
    """同内容不同 role 语义不同（用户问 / 助手答同一句），不折叠"""
    same = "重复一下"
    msgs = [Message.user(same), Message.assistant(same)]
    out = deterministic_compress_messages(
        msgs, DeterministicCompressConfig(dedup_enabled=True)
    )
    assert out[0].content == same
    assert out[1].content == same


def test_dedup_disabled_keeps_duplicates():
    same = "重复"
    msgs = [Message.user(same), Message.user(same)]
    out = deterministic_compress_messages(
        msgs,
        DeterministicCompressConfig(long_message_threshold=10000, dedup_enabled=False),
    )
    assert out[0].content == same
    assert out[1].content == same


def test_empty_content_messages_bypass_dedup():
    """空 content 不进 dedup 集合，多条空也不互相折叠"""
    msgs = [Message.user(""), Message.assistant(""), Message.user("")]
    out = deterministic_compress_messages(
        msgs, DeterministicCompressConfig(dedup_enabled=True)
    )
    assert all(m.content == "" for m in out)


# ==================== 组合场景 ====================

def test_long_then_dup_second_copy_gets_marker_after_truncation():
    """
    两条相同长内容：先都触发裁剪变相同短串，然后 dedup 折叠第二条。
    """
    long_same = "hello " * 5000  # 30000 chars, > 1000 阈值
    msgs = [Message.user(long_same), Message.assistant("中间"), Message.user(long_same)]
    out = deterministic_compress_messages(
        msgs,
        DeterministicCompressConfig(
            long_message_threshold=1000, head_chars=200, tail_chars=100, dedup_enabled=True
        ),
    )
    # 第一条被裁
    assert "已被规则压缩" in out[0].content
    # 第二条中间消息保留
    assert out[1].content == "中间"
    # 第三条：裁剪后 hash 与第一条一致 → 折叠
    assert out[2].content == _DUPLICATE_MARKER


def test_input_list_not_mutated():
    original = [Message.user("x" * 100), Message.user("x" * 100)]
    snapshot_content = [m.content for m in original]
    _ = deterministic_compress_messages(
        original, DeterministicCompressConfig(long_message_threshold=10, head_chars=5, tail_chars=5)
    )
    assert [m.content for m in original] == snapshot_content


def test_empty_input_returns_empty():
    assert deterministic_compress_messages([], DeterministicCompressConfig()) == []


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
