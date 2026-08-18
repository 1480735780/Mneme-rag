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
from rag.memory.config import MemoryProperties
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
        if record is None or not record.get("content") or not str(record["content"]).strip():
            return None
        return Message.system(record["content"])

    def decorate_if_needed(self, summary: Optional[Message]) -> Optional[Message]:
        return _decorate_summary(summary, self._template_loader)

    # ===================== 压缩主流程（对齐 Java doCompressIfNeeded） =====================

    def _do_compress(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        trigger_turns = self._properties.summary_start_turns
        max_turns = self._properties.history_keep_turns
        if max_turns <= 0 or trigger_turns <= 0:
            return

        lock_key = _SUMMARY_LOCK_PREFIX + f"{(user_id or '').strip()}:{(conversation_id or '').strip()}"
        if not self._try_lock(lock_key):
            return
        try:
            total = self._count_user_messages(conversation_id, user_id)
            if total < trigger_turns:
                return

            latest = self._find_latest_summary(conversation_id, user_id)
            latest_user_turns = self._list_latest_user_only_messages(conversation_id, user_id, max_turns)
            if not latest_user_turns:
                return
            history_start_id = latest_user_turns[-1].get("id")  # DESC 列表最后一条即最早
            if not history_start_id:
                return

            after_id = self._resolve_summary_start_id(conversation_id, user_id, latest)
            if after_id is not None and int(after_id) >= int(history_start_id):
                return  # 已摘要到原文窗口内，等重叠段滑出窗口再压缩

            # 摘要覆盖约一半原文窗口；只有这段重叠滑出窗口后才再次生成摘要
            summary_cutoff_id = latest_user_turns[(len(latest_user_turns) - 1) // 2].get("id")
            if not summary_cutoff_id:
                return

            to_summarize = self._list_messages_between_ids(
                conversation_id, user_id, after_id, summary_cutoff_id
            )
            if not to_summarize:
                return

            last_message_id = to_summarize[-1].get("id")
            if not last_message_id:
                return

            existing_summary = "" if latest is None else (latest.get("content") or "")
            summary = asyncio.run(
                self._summarize_messages(to_summarize, existing_summary)
            )
            if not summary or not summary.strip():
                return

            # 摘要行自带递增数字 id（毫秒时间戳+序号），供 find_latest 的 id DESC 排序；
            # 序号经 itertools.count 原子生成，跨会话并发压缩不产生重复 id
            self._db.insert_row(
                _T_CONVERSATION_SUMMARY,
                {
                    "id": f"{int(time.time() * 1000)}{next(self._seq_counter):06d}",
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "content": summary,
                    "last_message_id": last_message_id,
                    "create_time": _now_iso(),
                    "deleted": 0,
                },
            )
        except Exception:
            logger.exception(
                "摘要失败 - conversationId: %s, userId: %s", conversation_id, user_id
            )
        finally:
            self._unlock(lock_key)

    # ===================== LLM 摘要（对齐 Java summarizeMessages） =====================

    async def _summarize_messages(
        self,
        rows: List[dict],
        existing_summary: str,
    ) -> str:
        histories = self._to_history_messages(rows)
        if not histories:
            return existing_summary

        summary_max_chars = self._properties.summary_max_chars
        messages: List[Message] = [
            Message.system(
                self._prompt_resolver.render(
                    AgentPromptSlot.CONVERSATION_SUMMARY,
                    {"summary_max_chars": str(summary_max_chars)},
                )
            )
        ]
        if existing_summary and existing_summary.strip():
            messages.append(
                Message.assistant(
                    "历史摘要（仅用于合并去重，不得作为事实新增来源；若与本轮对话冲突，以本轮对话为准）：\n"
                    + existing_summary.strip()
                )
            )
        messages.extend(histories)
        messages.append(
            Message.user(
                "合并以上对话与历史摘要，去重后输出更新摘要。要求：严格≤"
                + str(summary_max_chars)
                + "字符；仅一行。"
            )
        )

        request = ChatRequest(
            messages=messages,
            temperature=0.3,
            topP=0.9,
            thinking=False,
        )
        try:
            return await self._llm.chat(request, tier=Tier.FAST)
        except Exception:
            logger.exception("对话记忆摘要生成失败, 消息数: %s", len(rows))
            return existing_summary

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
