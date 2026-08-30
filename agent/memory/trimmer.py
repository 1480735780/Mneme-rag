# -*- coding: utf-8 -*-
"""
agent.memory.trimmer - 会话上下文裁剪（对应 Java AgentContextTrimmer）

把过老的工具结果换成等长占位说明，不碰 IO 也不改列表长度；只替换工具结果
而不动工具调用，因此永远不会产生孤儿结果块。占位文案是纯陈述不带祈使句
——任何"需要请重新调用"都是往上下文里塞指令。

**与 Java 的模型差异（有意适配，ragent-new 为准绳的语义不变）**：
    Java（agentscope Java 2.0.2）把工具结果放在独立的 TOOL 角色消息里，按
    「assistant(带 tool_use) 开启循环 → tool 消息归属 → 用户消息/纯文本闭合」切分循环；
    agentscope Python 2.0.7 的 Msg 只有 user/assistant/system 三种角色，工具调用
    （ToolCallBlock）与结果（ToolResultBlock）内联在同一条 assistant 消息的 content 里。
    因此 Python 版：一条含 ToolCallBlock 的 assistant 消息即一个「工具循环」；
    调用与结果在**同消息内按 id 配对**判定循环是否闭合（有调用无结果 = 未闭合 = 保护）。
    循环保护、白名单、等长替换、最小回收量等语义与 Java 完全一致。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.memory.AgentContextTrimmer
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Set

from agentscope.message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)

logger = logging.getLogger(__name__)

# 纯陈述不带祈使句：任何"需要请重新调用"都是我们往上下文里塞指令
EVICTED_PREFIX = "[历史工具结果已省略，原长 "
EVICTED_SUFFIX = " 字符]"


@dataclass
class TrimResult:
    """裁剪结果：调用方据此感知是否变化（replacements 按 id(msg) 引用比对）"""

    reclaimed_chars: int = 0
    replacements: Dict[int, Msg] = field(default_factory=dict)

    def changed(self) -> bool:
        return self.reclaimed_chars > 0 and bool(self.replacements)


UNCHANGED = TrimResult()


@dataclass
class _Cycle:
    """一个工具循环：一条含工具调用的 assistant 消息（调用与结果同消息内按 id 配对）"""

    start_index: int
    call_ids: Set[str] = field(default_factory=set)
    result_indexes: List[int] = field(default_factory=list)  # (msg_index, block_index) 对
    resolved: Set[str] = field(default_factory=set)  # 已有结果配对的调用 id

    @property
    def pending_ids(self) -> Set[str]:
        return self.call_ids - self.resolved


@dataclass
class _Candidate:
    msg_index: int
    block: ToolResultBlock
    origin_chars: int
    reclaimable: int


class AgentContextTrimmer:
    """工具结果等长占位裁剪（就地修改传入的 context 列表）"""

    def __init__(self, properties):
        self._properties = properties

    def trim_in_place(self, context: List[Msg]) -> TrimResult:
        """就地裁剪并返回替换映射；不满足触发/下限条件时原样返回 UNCHANGED"""
        if not self._properties.enabled or not context:
            return UNCHANGED
        config = self._properties.tool_result
        total_chars = self._total_chars(context)
        if total_chars <= config.trigger_chars:
            return UNCHANGED

        cycles = self._split_cycles(context)
        protected = self._protected_cycles(context, cycles, config.keep_recent_cycles)
        candidates = self._collect_candidates(context, cycles, protected, config.evictable_tools)
        reclaimable = sum(c.reclaimable for c in candidates)
        # 够不着最小回收量就整次放弃：宁可这轮不省，也不为几百字符把前缀改一遍
        clear_at_least = math.ceil(total_chars * config.clear_at_least_ratio)
        if reclaimable < clear_at_least:
            logger.debug("上下文裁剪跳过, 总字符: %d, 可回收: %d, 下限: %d", total_chars, reclaimable, clear_at_least)
            return UNCHANGED

        replacements = self._apply(context, candidates)
        logger.info(
            "上下文裁剪完成, 总字符: %d -> %d, 命中消息: %d, 工具结果: %d",
            total_chars, total_chars - reclaimable, len(replacements), len(candidates),
        )
        return TrimResult(reclaimable, replacements)

    # ==================== 循环切分与保护 ====================

    def _split_cycles(self, context: List[Msg]) -> List[_Cycle]:
        """
        含工具调用的 assistant 消息即一个循环；调用与结果同消息内按 id 配对。

        注意：get_content_blocks(块类型) 不做类型过滤（返回全部块），必须显式
        isinstance 过滤——否则纯文本 assistant 消息也产出非空"调用列表"，被切成
        幽灵循环去吃 keep_recent_cycles 配额，导致本应受保护的真实循环被裁。
        """
        cycles: List[_Cycle] = []
        for index, msg in enumerate(context):
            if not self._is_assistant(msg):
                continue
            calls = [b for b in (msg.content or []) if isinstance(b, ToolCallBlock)]
            if not calls:
                continue
            cycle = _Cycle(start_index=index)
            results = [b for b in (msg.content or []) if isinstance(b, ToolResultBlock)]
            resolved_ids = {r.id for r in results}
            for call in calls:
                cycle.call_ids.add(call.id)
                if call.id in resolved_ids:
                    cycle.resolved.add(call.id)
            if results:
                cycle.result_indexes.append(index)
            cycles.append(cycle)
        return cycles

    def _protected_cycles(self, context: List[Msg], cycles: List[_Cycle], keep_recent_cycles: int) -> Set[int]:
        """
        本轮开出的循环与未闭合的循环一律保护且不占配额，keepRecentCycles 只在本轮之前计数。
        一个用户轮最多跑 maxIters 次推理，按「最近 N 个」计数会在模型合成答案前清掉它本轮自己查到的证据。
        """
        turn_start = self._last_user_index(context)
        result: Set[int] = set()
        kept = 0
        for i in range(len(cycles) - 1, -1, -1):
            cycle = cycles[i]
            if cycle.start_index > turn_start or cycle.pending_ids:
                result.add(i)
                continue
            if kept < keep_recent_cycles:
                result.add(i)
                kept += 1
        return result

    @staticmethod
    def _last_user_index(context: List[Msg]) -> int:
        """
        末条用户消息即本轮起点；摘要消息虽然也是 USER 但挂在头部，取最后一条不会认错。
        取不到用户消息返回 -1，此时全部循环落进保护区，宁可不省也不猜边界。
        """
        for i in range(len(context) - 1, -1, -1):
            if str(context[i].role).lower() == "user":
                return i
        return -1

    # ==================== 候选收集与替换 ====================

    def _collect_candidates(self, context: List[Msg], cycles: List[_Cycle],
                            protected: Set[int], evictable_tools: List[str]) -> List[_Candidate]:
        candidates: List[_Candidate] = []
        for c, cycle in enumerate(cycles):
            if c in protected:
                continue
            for msg_index in cycle.result_indexes:
                for block in self._result_blocks(context[msg_index]):
                    # name 为空串 = 框架级错误结果（事件流路径缺省 name，pydantic 必填 str
                    # 不会出现 None），判空顺带排除；不在白名单/已是占位的一并跳过
                    if not block.name or block.name not in evictable_tools or self._is_evicted(block):
                        continue
                    origin_chars = self._output_chars(block)
                    reclaimable = origin_chars - self._preview_chars(origin_chars)
                    if reclaimable > 0:
                        candidates.append(_Candidate(msg_index, block, origin_chars, reclaimable))
        return candidates

    def _apply(self, context: List[Msg], candidates: List[_Candidate]) -> Dict[int, Msg]:
        """
        等长原位替换：只换块不删不加，破坏性写留给真正做压缩的那一层。
        先全部重建再统一提交，重建阶段抛异常时 context 一个字节都没动。

        注意：get_content_blocks 返回副本而非 content 内的活引用（pydantic 语义），
        因此按结果块 id（= 调用 id，消息内唯一）匹配，不用对象身份。
        """
        hit: Dict[str, _Candidate] = {}  # 结果块 id → 候选
        touched: List[int] = []
        for candidate in candidates:
            hit[candidate.block.id] = candidate
            if candidate.msg_index not in touched:
                touched.append(candidate.msg_index)

        staged: Dict[int, Msg] = {}
        replacements: Dict[int, Msg] = {}
        for msg_index in touched:
            origin = context[msg_index]
            rebuilt = []
            for block in origin.content:
                candidate = hit.get(block.id) if isinstance(block, ToolResultBlock) else None
                rebuilt.append(self._evict(candidate) if candidate is not None else block)
            replaced = origin.model_copy(update={"content": rebuilt})
            staged[msg_index] = replaced
            replacements[id(origin)] = replaced
        for msg_index, replaced in staged.items():
            context[msg_index] = replaced
        return replacements

    def _evict(self, candidate: _Candidate) -> ToolResultBlock:
        """重建占位结果块：model_copy 保留全 id/name/metadata/state（漏掉 state 会洗掉工具挂起/失败状态）"""
        preview = self._preview(candidate.origin_chars)
        return candidate.block.model_copy(update={"output": [TextBlock(text=preview)]})

    # ==================== 字符计量与占位识别 ====================

    @staticmethod
    def _preview(origin_chars: int) -> str:
        return f"{EVICTED_PREFIX}{origin_chars}{EVICTED_SUFFIX}"

    def _preview_chars(self, origin_chars: int) -> int:
        return len(self._preview(origin_chars))

    @staticmethod
    def _result_blocks(msg: Msg) -> List[ToolResultBlock]:
        return [b for b in (msg.content or []) if isinstance(b, ToolResultBlock)]

    @staticmethod
    def _is_evicted(block: ToolResultBlock) -> bool:
        """靠占位文案自身识别已清理块：前缀判定不依赖任何外部约定"""
        output = block.output
        return (
            output is not None
            and len(output) == 1
            and isinstance(output[0], TextBlock)
            and output[0].text is not None
            and output[0].text.startswith(EVICTED_PREFIX)
        )

    def _total_chars(self, context: List[Msg]) -> int:
        return sum(self._chars_of(block) for msg in context for block in (msg.content or []))

    def _chars_of(self, block) -> int:
        """字符数只作为 token 的粗代理，非文本块按零计"""
        if isinstance(block, TextBlock):
            return self._length(block.text)
        if isinstance(block, ThinkingBlock):
            return self._length(block.thinking)
        if isinstance(block, ToolCallBlock):
            return self._length(block.name) + (len(str(block.input)) if block.input is not None else 0)
        if isinstance(block, ToolResultBlock):
            return self._output_chars(block)
        return 0

    def _output_chars(self, block: ToolResultBlock) -> int:
        if block.output is None:
            return 0
        return sum(self._chars_of(nested) for nested in block.output)

    @staticmethod
    def _length(value) -> int:
        return len(value) if value is not None else 0

    @staticmethod
    def _is_assistant(msg: Msg) -> bool:
        return str(msg.role).lower() == "assistant"
