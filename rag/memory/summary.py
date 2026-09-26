# -*- coding: utf-8 -*-
"""
对话记忆摘要 SPI + 进程内实现（对应 Java ConversationMemorySummaryService）

摘要 SPI 定义「压缩 / 读取最新摘要 / 装饰摘要」三个边界，语义对齐 Java：
    - compress_if_needed  → 仅在启用摘要（summary_enabled）且消息为 ASSISTANT 时触发压缩
    - load_latest_summary → 读取该会话最新摘要；无摘要返回 None（Java 无记录返回 null）
    - decorate_if_needed  → 把摘要内容包进 summary-wrapper 模板段、以 system 消息返回；
                            摘要为 None / 内容为空时原样返回（null 仍为 null）

MVP 阶段以 MemoryConversationMemorySummaryService 兜底：无 DB / 无 LLM / 无锁，
「压缩」退化为满足触发条件时调用注入的 summary_generator（旧摘要 + 触发消息 → 新摘要）
同步覆盖内存摘要；真实 JDBC 实现（步骤 5：summaryStartTurns 窗口 + cutoff +
CONVERSATION_SUMMARY 槽位渲染 + temp 0.3/topP 0.9 + Redisson 锁）后续注入同一 SPI 替换。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.memory.ConversationMemorySummaryService
    - com.nageoffer.ai.ragent.rag.core.memory.JdbcConversationMemorySummaryService
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from core.llm.chat import LLMService
from core.llm.enums import Tier
from core.llm.schema import ChatRequest, Message, Role
from rag.memory.compression.deterministic import (
    DeterministicCompressConfig,
    deterministic_compress_messages,
)
from rag.memory.config import MemoryProperties
from rag.memory.evidence import derive_evidence_refs
from rag.memory.flags import (
    CompressionFeatureFlags,
    CompressionVersion,
    should_run_v2_compression,
)
from rag.memory.merge import validate_merge
from rag.memory.self_healing import (
    HealingLevel,
    ParseFailureTracker,
    SelfHealingConfig,
    decide_healing_level,
)
from rag.memory.state import (
    ConversationState,
    parse_state_from_llm_output,
    render_state_to_content_snapshot,
    render_state_to_prompt_text,
)
from rag.memory.store import _T_CONVERSATION_SUMMARY
from rag.prompt.builder import AgentPromptResolver, AgentPromptSlot
from rag.prompt.formatter import CONTEXT_FORMAT_PATH, PromptTemplateLoader
from rag.source import CitationMarkup
from storage.database import Condition, DatabaseClient

logger = logging.getLogger(__name__)

# 摘要生成器：旧摘要（可为空串）+ 触发消息 → 新摘要（对齐 Java summarizeMessages 的调用方语义）
SummaryGenerator = Callable[[str, Message], str]


class ConversationMemorySummaryService(ABC):
    """对话记忆摘要服务接口（对应 Java ConversationMemorySummaryService）"""

    @abstractmethod
    def compress_if_needed(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> None:
        """
        判断是否需要压缩并触发

        仅当启用摘要（summary_enabled）且消息为 ASSISTANT 时触发压缩；否则 no-op。
        （Java 在此异步执行压缩任务并加分布式锁防并发，属 JDBC 实现细节）

        Args:
            conversation_id: 对话 ID
            user_id:         用户 ID
            message:         刚追加的消息（触发点：ASSISTANT 回答落库后）
        """
        ...

    @abstractmethod
    def load_latest_summary(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Message]:
        """
        读取该会话最新摘要

        Returns:
            Optional[Message]: 摘要（SYSTEM 角色）；无摘要返回 None
        """
        ...

    @abstractmethod
    def decorate_if_needed(self, summary: Optional[Message]) -> Optional[Message]:
        """
        把摘要内容包进 summary-wrapper 模板段（对应 Java decorateIfNeeded）

        Args:
            summary: 摘要消息

        Returns:
            Optional[Message]: SYSTEM 消息（包装后）；摘要为 None / 内容为空时原样返回
        """
        ...


class MemoryConversationMemorySummaryService(ConversationMemorySummaryService):
    """
    进程内摘要实现（MVP 兜底 / 测试注入）

    「压缩」退化为：满足触发条件且注入 summary_generator 时，
    （旧摘要 + 触发消息 → 新摘要）同步覆盖内存摘要；未注入生成器则仅满足触发条件、不生成。
    无 DB / 无 LLM / 无分布式锁——真实窗口/cutoff/锁逻辑见步骤 5 JDBC 实现。
    """

    def __init__(
        self,
        properties: Optional[MemoryProperties] = None,
        summary_generator: Optional[SummaryGenerator] = None,
        template_loader: Optional[PromptTemplateLoader] = None,
    ):
        self._properties = properties or MemoryProperties()
        self._summary_generator = summary_generator
        self._template_loader = template_loader or PromptTemplateLoader()
        self._summaries: Dict[Tuple[str, str], str] = {}

    def compress_if_needed(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> None:
        if not self._properties.summary_enabled:
            return
        if message is None or message.role != Role.ASSISTANT:
            return
        if self._summary_generator is None:
            return
        key = self._key(conversation_id, user_id)
        new_summary = self._summary_generator(self._summaries.get(key, ""), message)
        if new_summary and new_summary.strip():
            self._summaries[key] = new_summary

    def load_latest_summary(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Message]:
        content = self._summaries.get(self._key(conversation_id, user_id))
        if not content or not content.strip():
            return None
        return Message.system(content)

    def decorate_if_needed(self, summary: Optional[Message]) -> Optional[Message]:
        return _decorate_summary(summary, self._template_loader)

    @staticmethod
    def _key(conversation_id: Optional[str], user_id: Optional[str]) -> Tuple[str, str]:
        return (conversation_id or "", user_id or "")


def _decorate_summary(
    summary: Optional[Message],
    template_loader: PromptTemplateLoader,
) -> Optional[Message]:
    """摘要装饰（对齐 Java decorateIfNeeded）：空摘要原样返回，非空包进 summary-wrapper"""
    if summary is None or not summary.content or not summary.content.strip():
        return summary
    wrapped = template_loader.render_section(
        CONTEXT_FORMAT_PATH,
        "summary-wrapper",
        {"content": summary.content.strip()},
    )
    return Message.system(wrapped)


# 摘要压缩锁 key 前缀（对齐 Java SUMMARY_LOCK_PREFIX）
_SUMMARY_LOCK_PREFIX = "ragent:memory:summary:lock:"


class DatabaseConversationMemorySummaryService(ConversationMemorySummaryService):
    """
    关系库 + LLM 摘要实现（对应 Java JdbcConversationMemorySummaryService），Python 类名去 Jdbc 前缀

    压缩语义逐段对齐 Java doCompressIfNeeded：
        1. 仅 summary_enabled 且消息为 ASSISTANT 时后台触发（executor 提交，不阻塞调用方）；
        2. try_lock 防并发（MVP 进程内 per-key 锁；Redisson 分布式锁属后续 Redis 扩展）；
        3. 用户消息总数 < summary_start_turns → 不压缩；
        4. 摘要覆盖约一半原文窗口（cutoff = 最近 max_turns 条 user 消息的中位点），
           只有重叠段滑出窗口后才再次生成（afterId >= historyStartId 跳过）；
        5. LLM 合并历史摘要去重（CONVERSATION_SUMMARY 槽位、temp 0.3 / topP 0.9 / FAST 档）；
        6. 结果落 t_conversation_summary（last_message_id 记录摘要水位）。

    Args:
        db:              关系库访问抽象（DatabaseClient）
        llm_service:     LLM 服务（chat，FAST 档）
        prompt_resolver: 提示词解析器（render CONVERSATION_SUMMARY 槽位）
        properties:      记忆配置（MemoryProperties）
        template_loader: 模板加载器（decorate 用）
        executor:        压缩后台执行器（对应 Java memorySummaryExecutor；测试可注入同步执行器）
    """

    def __init__(
        self,
        db: DatabaseClient,
        llm_service: LLMService,
        prompt_resolver: AgentPromptResolver,
        properties: Optional[MemoryProperties] = None,
        template_loader: Optional[PromptTemplateLoader] = None,
        executor: Optional[Executor] = None,
    ):
        self._db = db
        self._llm = llm_service
        self._prompt_resolver = prompt_resolver
        self._properties = properties or MemoryProperties()
        self._template_loader = template_loader or PromptTemplateLoader()
        # 默认多 worker（对齐 ThreadPoolExecutor 默认启发式），避免单 worker 全局串行化
        # 所有会话压缩（一个慢 LLM 阻塞其他会话）；per-key 锁已保证同会话不并发，多 worker 安全
        self._executor = executor or ThreadPoolExecutor(
            max_workers=min(32, (os.cpu_count() or 1) + 4)
        )
        self._locks: Dict[str, threading.Lock] = {}
        # 摘要行自增序号（itertools.count 的 __next__ 在 CPython 下原子，跨会话并发不产生重复 id）
        self._seq_counter = itertools.count()
        # V2.1.1 P-08: 连续解析失败自愈状态（进程内，重启清零可接受）
        self._failure_tracker = ParseFailureTracker()
        self._healing_config = SelfHealingConfig()

    # ===================== SPI =====================

    def compress_if_needed(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> None:
        if not self._properties.summary_enabled:
            return
        if message is None or message.role != Role.ASSISTANT:
            return
        try:
            self._executor.submit(self._do_compress, conversation_id, user_id)
        except Exception:
            logger.exception(
                "对话记忆摘要异步任务提交失败 - conversationId: %s, userId: %s",
                conversation_id,
                user_id,
            )

    def load_latest_summary(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Message]:
        record = self._find_latest_summary(conversation_id, user_id)
        if record is None:
            return None
        return self._render_latest_summary_message(record, conversation_id, user_id)

    def _render_latest_summary_message(
        self,
        record: dict,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Message]:
        """
        按 summary_version 分派渲染：
            v2 + structured_content 有效 → ConversationState + 派生 evidence 动态合成
            其他                       → 回落 content 原文（V1 兼容路径）
        """
        version = (record.get("summary_version") or "").strip().lower()
        if version == "v2":
            state = self._load_state_from_record(record)
            if state is not None and not state.is_empty():
                evidence_refs = derive_evidence_refs(
                    self._db, conversation_id, user_id
                )
                rendered = render_state_to_prompt_text(state, evidence_refs)
                if rendered and rendered.strip():
                    return Message.system(rendered)
                # 渲染空 fallback 到 content 快照（防御式，正常不应命中）
        content = record.get("content")
        if not content or not str(content).strip():
            return None
        return Message.system(str(content))

    @staticmethod
    def _load_state_from_record(record: dict) -> Optional[ConversationState]:
        """从摘要行的 structured_content 列还原 ConversationState（兼容 dict / JSON str）"""
        structured = record.get("structured_content")
        if not structured:
            return None
        if isinstance(structured, str):
            try:
                structured = json.loads(structured)
            except json.JSONDecodeError:
                return None
        if not isinstance(structured, dict):
            return None
        try:
            return ConversationState.model_validate(structured)
        except Exception:
            return None

    def decorate_if_needed(self, summary: Optional[Message]) -> Optional[Message]:
        return _decorate_summary(summary, self._template_loader)

    # ===================== 压缩主流程（对齐 Java doCompressIfNeeded） =====================

    def _do_compress(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        """执行压缩对话记忆摘要。"""
        # 检查配置：窗口或触发阈值配置非法时，直接结束，不执行摘要。
        trigger_turns = self._properties.summary_start_turns
        max_turns = self._properties.history_keep_turns
        if max_turns <= 0 or trigger_turns <= 0:
            return
        #获取会话锁，如果同一个会话已经有摘要任务在执行，新任务直接放弃，防止并发重复压缩。
        lock_key = _SUMMARY_LOCK_PREFIX + f"{(user_id or '').strip()}:{(conversation_id or '').strip()}"
        if not self._try_lock(lock_key):
            return
        try:
            # 检查会话是否已压缩到触发阈值，未达到则直接结束。
            total = self._count_user_messages(conversation_id, user_id)
            if total < trigger_turns:
                return
            latest = self._find_latest_summary(conversation_id, user_id)
            # V2.1.1 P-07: Feature Flag 灰度决策
            # 全局 v1（关闭 V2 生产）+ 本会话尚无 V2 摘要 → 跳过本次压缩
            # 本会话已有 V2 摘要 → 无论全局 flag 是什么都继续（会话级 sticky）
            latest_version = (latest or {}).get("summary_version") if latest else None
            flags = CompressionFeatureFlags(
                default_version=CompressionVersion.parse(
                    self._properties.context_compression_version
                )
            )
            if not should_run_v2_compression(flags, latest_version):
                logger.debug(
                    "V2 压缩被 feature flag 关闭, skip - conversationId: %s, userId: %s",
                    conversation_id,
                    user_id,
                )
                return
            #读取最近 8 条 User 消息
            latest_user_turns = self._list_latest_user_only_messages(conversation_id, user_id, max_turns)
            if not latest_user_turns:
                return
            # V2.1.1 bug fix (C1/C2 benchmark)：原检查用 history_start_id（窗口最老用户）
            # 语义是"摘要追上窗口起点就等再压"——但短会话永远追不上，任何修正都进不去。
            # 正确语义应基于本次压缩右边界 cutoff：cutoff 若还在 after_id 之前，说明
            # 没有新消息把 cutoff 推后，才跳过；只要 cutoff 前进了一点就该继续压。
            after_id = self._resolve_summary_start_id(conversation_id, user_id, latest)

            # 摘要覆盖约一半原文窗口；只有这段重叠滑出窗口后才再次生成摘要，summary_cutoff_id是本次准备摘要到哪里的分界线
            summary_cutoff_id = latest_user_turns[(len(latest_user_turns) - 1) // 2].get("id")
            if not summary_cutoff_id:
                return

            # V2.1.1 修复后的 overlap 检查：cutoff 未超过上次摘要水位才跳过
            if after_id is not None and int(summary_cutoff_id) <= int(after_id):
                return  # cutoff 已被上次摘要覆盖，等有新窗口再压

            to_summarize = self._list_messages_between_ids(
                conversation_id, user_id, after_id, summary_cutoff_id
            )
            if not to_summarize:
                return

            last_message_id = to_summarize[-1].get("id")
            if not last_message_id:
                return

            existing_summary = "" if latest is None else (latest.get("content") or "")
            previous_state = self._resolve_previous_state(latest, existing_summary)

            # ==================== V2.1.1 P-08 三级自愈 ====================
            count = self._failure_tracker.current_count(user_id or "", conversation_id or "")
            level = decide_healing_level(count, self._healing_config)

            if level == HealingLevel.LEVEL_3_FORCE_ADVANCE:
                # 不 LLM，直接强推水位：写一条 force_advance 行，保留旧 state 内容
                self._write_force_advance_row(
                    conversation_id, user_id, last_message_id, previous_state
                )
                self._failure_tracker.record_success(
                    user_id or "", conversation_id or ""
                )
                logger.warning(
                    "摘要 LLM 连续失败达 Level 3, 强推水位避免会话卡死 - "
                    "conversationId: %s, userId: %s, prior_failures: %s",
                    conversation_id,
                    user_id,
                    count,
                )
                return

            new_state = asyncio.run(
                self._summarize_messages(
                    to_summarize, previous_state, healing_level=level
                )
            )
            if new_state is None or new_state.is_empty():
                new_count = self._failure_tracker.record_failure(
                    user_id or "", conversation_id or ""
                )
                logger.warning(
                    "对话摘要解析失败或结果为空, 保留旧 state - "
                    "conversationId: %s, userId: %s, level: %s, cumulative_failures: %s",
                    conversation_id,
                    user_id,
                    level.value,
                    new_count,
                )
                return

            # V2.1.1 P-02：per-slot merge 违规校验（progress / critical_context 丢失比例超阈值时保留旧 state）
            # V2.1.1 P-06 补充：V1→V2 首次迁移不适用 merge 校验——previous_state 的 critical_context
            # 是整段 V1 自由文本，与新 LLM 输出的真实 critical_context 语义结构完全不同，
            # 走 merge 校验会被误判 100% 丢失。只有 V2→V2 稳态增量才需要保护。
            # V2.1.1 P-08 补充：LEVEL_2_V1_FALLBACK 输出的 state 是"critical_context=[纯文本]"
            # 的包装形态，与严格 merge 语义不匹配，也跳过校验。
            is_v2_stable_state = (
                latest is not None
                and (latest.get("summary_version") or "").strip().lower() == "v2"
            )
            if is_v2_stable_state and level != HealingLevel.LEVEL_2_V1_FALLBACK:
                merge_result = validate_merge(previous_state, new_state)
                if not merge_result.ok:
                    logger.warning(
                        "对话摘要 merge 违规, 保留旧 state - conversationId: %s, userId: %s, "
                        "progress_drop=%.2f, critical_shrink=%.2f, reasons=%s",
                        conversation_id,
                        user_id,
                        merge_result.progress_drop_ratio,
                        merge_result.critical_context_shrink_ratio,
                        merge_result.violations,
                    )
                    return

            # 双写：content 为无 evidence 的文本快照（V1 消费者兜底 + 迁移审计），
            # structured_content 为 ConversationState JSON（load 时结合最新 evidence 动态渲染）
            row_version = (
                "v1_fallback"
                if level == HealingLevel.LEVEL_2_V1_FALLBACK
                else "v2"
            )
            content_snapshot = render_state_to_content_snapshot(new_state)
            self._db.insert_row(
                _T_CONVERSATION_SUMMARY,
                {
                    "id": f"{int(time.time() * 1000)}{next(self._seq_counter):06d}",
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "content": content_snapshot,
                    "structured_content": new_state.to_json_dict(),
                    "summary_version": row_version,
                    "last_message_id": last_message_id,
                    # V2.1.1 P-09 观测：本轮 LLM 调用一次（未来加 self-repair 时递增）
                    "attempt_count": 1,
                    "healing_level": level.value,
                    "create_time": _now_iso(),
                    "deleted": 0,
                },
            )
            # 走通完整链路 → 归零失败计数
            self._failure_tracker.record_success(user_id or "", conversation_id or "")
        except Exception:
            logger.exception(
                "摘要失败 - conversationId: %s, userId: %s", conversation_id, user_id
            )
        finally:
            self._unlock(lock_key)

    # ===================== LLM 摘要（V2.1.1 P-01 结构化 4-slot 输出 + P-08 三级自愈） =====================

    async def _summarize_messages(
        self,
        rows: List[dict],
        previous_state: Optional[ConversationState],
        healing_level: HealingLevel = HealingLevel.LEVEL_0_FAST,
    ) -> Optional[ConversationState]:
        """
        把「历史 ConversationState + 本轮待压缩消息」交给 LLM，产出新的 ConversationState。

        按 healing_level 分派：
            LEVEL_0_FAST       : FAST 档 + 严格 JSON prompt + 4-slot parse
            LEVEL_1_STANDARD   : 升 STANDARD 档，其余同 L0（应对小模型 JSON 遵循不稳）
            LEVEL_2_V1_FALLBACK: 用 V1 兼容自由文本 prompt，把结果整段包进 critical_context

        Returns:
            Optional[ConversationState]:
                成功返回新 state（L2 时是 wrap 出来的伪 state）
                LLM 调用异常 / 输出非法 / 解析失败 → 返回 None，调用方按 P-08 累计失败计数
        """
        histories = self._to_history_messages(rows)
        if not histories:
            # 无有效消息（rows 全被 role/content 过滤掉）→ 视为不压缩，返回 None
            # 避免旧 state 被原样再写一遍并推进水位（那会让"水位线在动但内容没变"）
            return None

        # V2.1.1 P-03: 规则先行、LLM 后置 — Deterministic Compression 在喂摘要模型前跑一次
        # 未来 Split Turn 接入时位置在此之后（Cut → Deterministic → Split → Summary）
        histories = deterministic_compress_messages(
            histories, DeterministicCompressConfig()
        )

        if healing_level == HealingLevel.LEVEL_2_V1_FALLBACK:
            return await self._summarize_via_v1_fallback(histories, previous_state)

        tier = (
            Tier.STANDARD
            if healing_level == HealingLevel.LEVEL_1_STANDARD
            else Tier.FAST
        )

        summary_max_chars = self._properties.summary_max_chars
        messages: List[Message] = [
            Message.system(
                self._prompt_resolver.render(
                    AgentPromptSlot.CONVERSATION_SUMMARY,
                    {"summary_max_chars": str(summary_max_chars)},
                )
            )
        ]
        if previous_state is not None and not previous_state.is_empty():
            previous_json = json.dumps(
                previous_state.to_json_dict(), ensure_ascii=False, indent=None
            )
            messages.append(
                Message.assistant(
                    "历史状态（JSON 结构，仅作为合并基底，不得作为事实新增来源；"
                    "若与本轮对话冲突，以本轮对话为准）：\n" + previous_json
                )
            )
        messages.extend(histories)
        messages.append(
            Message.user(
                "按系统提示的 4-slot JSON 结构输出更新后的会话状态。"
                "整体长度不超过 " + str(summary_max_chars) + " 字，"
                "严格 JSON，不要任何解释或代码围栏。"
            )
        )

        request = ChatRequest(
            messages=messages,
            temperature=0.3,
            topP=0.9,
            thinking=False,
        )
        try:
            raw = await self._llm.chat(request, tier=tier)
        except Exception:
            logger.exception(
                "对话记忆摘要 LLM 调用失败, 消息数: %s, level: %s",
                len(rows),
                healing_level.value,
            )
            return None

        parsed = parse_state_from_llm_output(raw)
        if parsed is None:
            logger.warning(
                "对话摘要 JSON 解析失败 (level=%s), 原始输出前 200 字: %s",
                healing_level.value,
                (raw or "")[:200],
            )
        return parsed

    async def _summarize_via_v1_fallback(
        self,
        histories: List[Message],
        previous_state: Optional[ConversationState],
    ) -> Optional[ConversationState]:
        """
        P-08 Level 2：让 LLM 用 V1 兼容自由文本模式生成摘要。
        结果整段塞进 ConversationState.critical_context，其它 slot 保留 previous_state 的
        （若有），形成"退化模式仍保持结构一致"的过渡态。
        """
        summary_max_chars = self._properties.summary_max_chars
        messages: List[Message] = [
            Message.system(
                "你是会话摘要助手。请把下面「历史摘要 + 本轮对话」合并成一段自然语言摘要，"
                f"严格不超过 {summary_max_chars} 字，输出纯文本，不要 JSON、不要代码围栏、不要解释。"
            )
        ]
        if previous_state is not None and not previous_state.is_empty():
            # 把 previous_state 展平成人类可读文本喂给 LLM
            legacy_text = render_state_to_content_snapshot(previous_state)
            messages.append(
                Message.assistant("历史摘要（自然语言，仅供合并参考）：\n" + legacy_text)
            )
        messages.extend(histories)
        messages.append(
            Message.user(
                f"输出更新后的自然语言摘要，≤{summary_max_chars} 字，仅一段。"
            )
        )
        request = ChatRequest(
            messages=messages,
            temperature=0.3,
            topP=0.9,
            thinking=False,
        )
        try:
            raw = await self._llm.chat(request, tier=Tier.FAST)
        except Exception:
            logger.exception("V1 fallback 摘要 LLM 调用失败")
            return None
        if not raw or not raw.strip():
            logger.warning("V1 fallback 输出为空")
            return None

        # 包装为 state：critical_context 用新文本替换（老 critical_context 已合进新摘要）
        base_progress = list(previous_state.progress) if previous_state else []
        base_open = list(previous_state.open_questions) if previous_state else []
        base_goal = previous_state.user_goal if previous_state else ""
        return ConversationState(
            user_goal=base_goal,
            progress=base_progress,
            open_questions=base_open,
            critical_context=[raw.strip()],
        )

    def _resolve_previous_state(
        self,
        latest: Optional[dict],
        existing_summary_text: str,
    ) -> Optional[ConversationState]:
        """
        从 t_conversation_summary 最新行还原 previous_state 供下一轮 merge。

        三种情况（V2.1.1 P-06 Lazy Migration 语义）：
            - latest 为 None → 首次压缩，返回 None
            - summary_version='v2' → 解析 structured_content 为 ConversationState
            - summary_version 缺失/'v1' → V1 自由文本原样放入 critical_context
              （其他 slot 留空，下一次压缩时 LLM 从 raw 消息中重建）
        """
        if latest is None:
            return None

        version = (latest.get("summary_version") or "").strip().lower()
        structured = latest.get("structured_content")

        if version == "v2" and structured:
            # SQL 后端返回 str，InMemory 后端返回 dict
            if isinstance(structured, str):
                try:
                    structured = json.loads(structured)
                except json.JSONDecodeError:
                    logger.warning("structured_content JSON 反序列化失败, 回落 V1 语义")
                    return self._wrap_legacy_summary_as_state(existing_summary_text)
            if isinstance(structured, dict):
                try:
                    return ConversationState.model_validate(structured)
                except Exception:
                    logger.warning("ConversationState 校验失败, 回落 V1 语义")
                    return self._wrap_legacy_summary_as_state(existing_summary_text)

        return self._wrap_legacy_summary_as_state(existing_summary_text)

    @staticmethod
    def _wrap_legacy_summary_as_state(summary_text: str) -> Optional[ConversationState]:
        """V1 自由文本 → V2 state 的最小包装（整段进 critical_context）"""
        if not summary_text or not summary_text.strip():
            return None
        return ConversationState(
            user_goal="",
            progress=[],
            open_questions=[],
            critical_context=[summary_text.strip()],
        )

    def _write_force_advance_row(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        last_message_id: str,
        previous_state: Optional[ConversationState],
    ) -> None:
        """
        P-08 Level 3：不跑 LLM 只推水位。写一行 summary_version='force_advance'
        保留旧 state（若有）或空 state；下一次压缩仍会走 L0 尝试。
        """
        state = previous_state or ConversationState()
        content_snapshot = render_state_to_content_snapshot(state)
        self._db.insert_row(
            _T_CONVERSATION_SUMMARY,
            {
                "id": f"{int(time.time() * 1000)}{next(self._seq_counter):06d}",
                "conversation_id": conversation_id,
                "user_id": user_id,
                "content": content_snapshot,
                "structured_content": state.to_json_dict(),
                "summary_version": "force_advance",
                "last_message_id": last_message_id,
                # V2.1.1 P-09: L3 不调 LLM, attempt_count=0; 记录 level 供 dashboard 定位
                "attempt_count": 0,
                "healing_level": HealingLevel.LEVEL_3_FORCE_ADVANCE.value,
                "create_time": _now_iso(),
                "deleted": 0,
            },
        )

    @staticmethod
    def _to_history_messages(rows: List[dict]) -> List[Message]:
        """行 → 历史消息（对齐 Java toHistoryMessages）：仅 user/assistant，assistant 剥 CitationMarkup"""
        result: List[Message] = []
        for row in rows:
            if not row or not row.get("content") or not str(row["content"]).strip():
                continue
            role = str(row.get("role") or "").lower()
            if role == "user":
                result.append(Message.user(row["content"]))
            elif role == "assistant":
                result.append(Message.assistant(CitationMarkup.strip(row["content"])))
        return result

    # ===================== 查询辅助（对齐 Java ConversationGroupServiceImpl） =====================

    def _count_user_messages(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> int:
        if _blank(conversation_id) or _blank(user_id):
            return 0
        rows = self._db.select_rows(
            "t_message",
            columns=["id"],
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("role", "user"),
                Condition.eq("deleted", 0),
            ],
        )
        return len(rows)

    def _list_latest_user_only_messages(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        limit: int,
    ) -> List[dict]:
        if _blank(conversation_id) or _blank(user_id) or limit <= 0:
            return []
        return self._db.select_rows(
            "t_message",
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("role", "user"),
                Condition.eq("deleted", 0),
            ],
            order_by=[("create_time", "desc")],
            limit=limit,
        )

    def _list_messages_between_ids(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        after_id: Optional[str],
        before_id: Optional[str],
    ) -> List[dict]:
        if _blank(conversation_id) or _blank(user_id):
            return []
        conditions = [
            Condition.eq("conversation_id", conversation_id),
            Condition.eq("user_id", user_id),
            Condition.in_("role", ["user", "assistant"]),
            Condition.eq("deleted", 0),
        ]
        if after_id is not None:
            conditions.append(Condition.gt("id", after_id))
        if before_id is not None:
            conditions.append(Condition.lt("id", before_id))
        return self._db.select_rows(
            "t_message",
            where=conditions,
            order_by=[("id", "asc")],
        )

    def _find_max_message_id_at_or_before(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        at: str,
    ) -> Optional[str]:
        if _blank(conversation_id) or _blank(user_id) or not at:
            return None
        rows = self._db.select_rows(
            "t_message",
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", 0),
                Condition.le("create_time", at),
            ],
            order_by=[("id", "desc")],
            limit=1,
        )
        return rows[0].get("id") if rows else None

    def _find_latest_summary(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[dict]:
        #
        if _blank(conversation_id) or _blank(user_id):
            return None
        rows = self._db.select_rows(
            _T_CONVERSATION_SUMMARY,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", 0),
            ],
            order_by=[("id", "desc")],
            limit=1,
        )
        return rows[0] if rows else None

    def _resolve_summary_start_id(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        latest: Optional[dict],
    ) -> Optional[str]:
        """摘要水位（对齐 Java resolveSummaryStartId）：优先 last_message_id，否则按摘要时间回溯最大消息 ID"""
        if latest is None:
            return None
        if latest.get("last_message_id"):
            return latest["last_message_id"]
        after = latest.get("update_time") or latest.get("create_time")
        return self._find_max_message_id_at_or_before(conversation_id, user_id, after)

    # ===================== 进程内锁（对应 Redisson tryLock） =====================

    def _try_lock(self, key: str) -> bool:
        lock = self._locks.setdefault(key, threading.Lock())
        return lock.acquire(blocking=False)

    def _unlock(self, key: str) -> None:
        lock = self._locks.get(key)
        if lock is None:
            return
        try:
            lock.release()
        except RuntimeError:
            pass  # 未持有时释放，忽略（对应 Redisson unlock 的幂等语义）


def _now_iso() -> str:
    """当前时间 ISO 字符串（摘要时间戳列）"""
    return datetime.now().isoformat()


def _blank(value: Optional[str]) -> bool:
    """空 / 纯空白判定（对应 Java StrUtil.isBlank）"""
    return value is None or not str(value).strip()
