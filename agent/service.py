# -*- coding: utf-8 -*-
"""
agent.service - Agent 服务层（对应 Java AgentConversationServiceImpl / AgentChatServiceImpl + SseEmitterSender）

- `AgentSseSender`：SseQueue 上的 Agent 协议发送器（事件名 = AgentSSEEventType.value，
  载荷 to_dict camelCase；与 workflow 协议两套分立但共用同一 SseQueue/StreamingResponse 设施）。
- `AgentConversationService`：会话/消息编排（touchConversation 的先查后建 + 残留清理 +
  唯一键竞争回落、轮数统计、loadRecentTurns 供改写指代消解、删除释放运行态）。
- `AgentChatService`：流式编排——闸门先于一切副作用 → META → 会话/消息落库 →
  Agent 构建（状态装载）→ reply_stream 消费 → 状态回存 → 三条收尾路。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.service.impl.AgentConversationServiceImpl
    com.nageoffer.ai.ragent.agent.service.impl.AgentChatServiceImpl
    com.nageoffer.ai.ragent.framework.web.SseEmitterSender
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from agentscope.message import Msg, TextBlock
from common.exception.business import ClientException
from common.util.snowflake import default_generator
from common.web.sse import SseQueue, encode_event
from rag.service.stream.protocol import CompletionPayload as HouseCompletionPayload, MessageStatus

from agent.config import AgentProperties
from agent.dao import TITLE_MAX_LENGTH, AgentConversationDao, AgentMessageDao
from agent.models import AgentMetaPayload, AgentMessageStatus
from agent.provider import ReActAgentProvider
from agent.run_gate import AgentRunGate
from agent.run_handle import AgentRunHandle
from agent.state_store import PgAgentStateStore
from agent.stream_bridge import AgentStreamEventBridge

logger = logging.getLogger(__name__)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
REWRITE_CONTEXT_TURNS = 2  # 改写只用近期轮次消解指代，取多了也被 buildRewriteRequest 截断
RENAME_MAX_LENGTH = 128  # 超长截断而非拒绝（对齐 Java rename 的 StrUtil.sub 行为）


class AgentSseSender:
    """SseQueue 上的 Agent 协议发送器（对应 Java SseEmitterSender）"""

    def __init__(self, queue: SseQueue):
        self._queue = queue

    def send_event(self, event_type: str, payload: Any) -> None:
        data = payload if isinstance(payload, str) else json.dumps(payload.to_dict(), ensure_ascii=False)
        self._queue.push(encode_event(event_type, data))

    def send_raw(self, event_type: str, data: str) -> None:
        self._queue.push(encode_event(event_type, data))

    def fail(self, error: BaseException) -> None:
        self.send_raw("error", json.dumps({"error": str(error)}, ensure_ascii=False))

    def complete(self) -> None:
        self._queue.close()

    def close(self) -> None:
        """house StreamTaskManager 取消流会调用 sender.close()（等价 complete）"""
        self._queue.close()


class AgentConversationService:
    """会话/消息编排（t_agent_conversation / t_agent_message 之上）"""

    def __init__(self, db: Any, state_store: PgAgentStateStore, run_gate: AgentRunGate):
        self._conversation_dao = AgentConversationDao(db)
        self._message_dao = AgentMessageDao(db)
        self._state_store = state_store
        self._run_gate = run_gate

    def touch_conversation(self, conversation_id: str, user_id: str, question: str) -> str:
        """会话不存在则建（截断首问作标题，不走 LLM），存在则刷新 last_time；返回标题"""
        existing = self._conversation_dao.find_active(conversation_id, user_id)
        if existing is not None:
            self._conversation_dao.touch(conversation_id, user_id)
            return existing.get("title") or ""
        # 会话行不在了，同号的状态与消息只可能是删除时在途流的残骸：清在建行之前
        self._purge_residue(conversation_id, user_id)
        title = (question or "").strip()[:TITLE_MAX_LENGTH]
        try:
            self._conversation_dao.insert(conversation_id, user_id, title)
        except Exception:
            # 唯一键竞争（dao 层契约，P0 登记）：落败方重查赢家；查不到说明冲突另有来源
            winner = self._conversation_dao.find_active(conversation_id, user_id)
            if winner is None:
                raise
            self._conversation_dao.touch(conversation_id, user_id)
            return winner.get("title") or ""
        return title

    def add_user_message(self, conversation_id: str, user_id: str, content: str) -> str:
        return self._message_dao.insert_user_message(conversation_id, user_id, content)

    def add_assistant_message(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
        thinking_content: Optional[str],
        blocks: Optional[List[Dict[str, Any]]],
        reply_to_message_id: Optional[str],
        status: AgentMessageStatus,
    ) -> str:
        return self._message_dao.insert_assistant_message(
            conversation_id, user_id, content,
            thinking_content=thinking_content, blocks=blocks,
            reply_to_message_id=reply_to_message_id, message_status=status.value,
        )

    def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """会话列表（last_time 倒序 + 每会话轮数 = 用户提问数，一次 in 查询避免 N+1）"""
        rows = self._conversation_dao.list_by_user(user_id)
        turn_counts = self._message_dao.count_user_turns(
            user_id, [row["conversation_id"] for row in rows]
        )
        return [
            {
                "conversationId": row["conversation_id"],
                "title": row.get("title") or "",
                "lastTime": row.get("last_time"),
                "turns": turn_counts.get(row["conversation_id"], 0),
            }
            for row in rows
        ]

    def rename(self, conversation_id: str, user_id: str, title: str) -> None:
        """重命名（对齐 Java rename：空标题 ClientException、会话不存在 ClientException、超长截断）"""
        trimmed = (title or "").strip()
        if not trimmed:
            raise ClientException("会话标题不能为空")
        if self._conversation_dao.find_active(conversation_id, user_id) is None:
            raise ClientException("会话不存在")
        self._conversation_dao.rename(conversation_id, user_id, trimmed[:RENAME_MAX_LENGTH])

    def list_messages(self, conversation_id: str, user_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row.get("content"),
                "thinkingContent": row.get("thinking_content"),
                "blocks": row.get("blocks"),
                "messageStatus": row.get("message_status"),
                "createTime": row.get("create_time"),
            }
            for row in self._message_dao.list_by_conversation(conversation_id, user_id)
        ]

    def load_recent_turns(self, conversation_id: str, user_id: int, turns: int = REWRITE_CONTEXT_TURNS) -> List[Any]:
        """
        按 replyToMessageId 配成「提问 + 回答」对取最近 N 对，只取正文
        （blocks 里的工具结果动辄上万字；对齐 Java loadRecentTurns 的意图）
        """
        rows = self._message_dao.list_by_conversation(conversation_id, user_id)
        by_id = {row["id"]: row for row in rows}
        pairs: List[tuple] = []
        for row in reversed(rows):
            if row["role"] != ROLE_ASSISTANT:
                continue
            reply_to = row.get("reply_to_message_id")
            question = by_id.get(reply_to) if reply_to else None
            if question is None or not self._usable_answer(row):
                continue
            pairs.append((question, row))
            if len(pairs) >= turns:
                break
        messages: List[Any] = []
        for question, answer in reversed(pairs):
            messages.append(Msg(name="user", role="user", content=[TextBlock(text=question.get("content") or "")]))
            messages.append(Msg(name="assistant", role="assistant", content=[TextBlock(text=answer.get("content") or "")]))
        return messages

    def delete(self, conversation_id: str, user_id: str) -> None:
        """软删会话 + 释放运行态（状态存储整会话删除；对齐 Java delete）"""
        if self._conversation_dao.soft_delete(conversation_id, user_id):
            self._release_runtime_state(conversation_id, user_id)

    def delete_batch(self, conversation_ids: List[str], user_id: str) -> None:
        for conversation_id in conversation_ids or []:
            self.delete(conversation_id, user_id)

    def _usable_answer(self, row: Dict[str, Any]) -> bool:
        return (
            row.get("message_status") == AgentMessageStatus.NORMAL.value
            and bool((row.get("content") or "").strip())
        )

    def _purge_residue(self, conversation_id: str, user_id: str) -> None:
        """清状态与消息残骸（失败就不建行的语义由调用顺序保证）；InMemory 本地即全部节点"""
        try:
            self._state_store.delete(user_id, conversation_id)
            self._message_dao.mark_deleted_all(conversation_id, user_id)
        except Exception:  # noqa: BLE001 清残骸失败不阻断建行（下次重开还会再清）
            logger.warning("会话残留清理失败, conversationId: %s", conversation_id, exc_info=True)

    def _release_runtime_state(self, conversation_id: str, user_id: str) -> None:
        try:
            self._state_store.delete(user_id, conversation_id)
        except Exception:  # noqa: BLE001
            logger.warning("会话状态释放失败, conversationId: %s", conversation_id, exc_info=True)


class AgentChatService:
    """Agent 流式对话编排：会话落库 → ReAct 事件流 → SSE 桥 → 取消接线"""

    def __init__(
        self,
        provider: ReActAgentProvider,
        conversation_service: AgentConversationService,
        run_gate: AgentRunGate,
        task_manager: Any,
        state_store: PgAgentStateStore,
        properties: AgentProperties,
    ):
        self._provider = provider
        self._conversation_service = conversation_service
        self._run_gate = run_gate
        self._task_manager = task_manager
        self._state_store = state_store
        self._properties = properties

    async def stream_chat(
        self,
        question: str,
        user_id: str,
        conversation_id: Optional[str],
        sender: AgentSseSender,
    ) -> None:
        """
        一次流式对话（对应 Java streamChat）：闸门先于一切副作用；
        启动段失败就地归还闸门并撤销任务登记，否则该用户被挡到 TTL 过期。
        """
        actual_conversation_id = (conversation_id or "").strip() or str(default_generator.next_id())
        task_id = str(default_generator.next_id())

        release: Optional[Any] = None
        started = False
        try:
            release = await self._run_gate.acquire(user_id, task_id, actual_conversation_id)
            await self._start_run(question, user_id, actual_conversation_id, task_id, sender, release)
            started = True
        except BaseException:
            if not started and release is not None:
                await release()
                self._task_manager.unregister(task_id)
            raise

    async def _start_run(
        self,
        question: str,
        user_id: str,
        conversation_id: str,
        task_id: str,
        sender: AgentSseSender,
        release: Any,
    ) -> None:
        sender.send_event("meta", AgentMetaPayload(conversation_id, task_id))
        title = self._conversation_service.touch_conversation(conversation_id, user_id, question)
        question_message_id = self._conversation_service.add_user_message(conversation_id, user_id, question)

        run_handle = AgentRunHandle(task_id, sender, self._task_manager)
        # 收尾钩子：归还闸门（值比对防误删新槽）——记忆常驻才需要驱逐，Python 状态按轮装载/回存，无需驱逐
        loop = asyncio.get_running_loop()
        run_handle.on_release(lambda: loop.create_task(release()))

        # 实例与目录快照成对取出：事件展示名与 Toolkit 出自同一次解析（对齐 Java）；
        # get_agent 为 async（Toolkit 构建异步，漏 await 即协程注入，见 provider 模块注释）
        active = await self._provider.get_agent(user_id, conversation_id)
        bridge = AgentStreamEventBridge(
            run_handle=run_handle,
            conversation_service=self._conversation_service,
            catalog=active.catalog,
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            reply_to_message_id=question_message_id,
        )
        # 取消接线（对齐 Java register(taskId, userId, bridge::finishCancelledStream)）：
        # supplier 先让桥收尾（settle-once：落库 + CANCEL/DONE + 关队列），返回的 house 载荷
        # 供 task_manager 的补发帧——此时队列已关，SseQueue 丢弃之，天然幂等
        def on_cancel_supplier():
            bridge.finish_cancelled_stream()
            return HouseCompletionPayload(message_status=MessageStatus.INTERRUPTED)

        self._task_manager.register(task_id, sender, on_cancel_supplier)
        self._task_manager.bind_task(task_id, run_handle.interrupt_upstream)

        asyncio.create_task(self._run_agent(active, question, user_id, conversation_id, run_handle, bridge))

    async def _run_agent(
        self,
        active: Any,
        question: str,
        user_id: str,
        conversation_id: str,
        run_handle: AgentRunHandle,
        bridge: AgentStreamEventBridge,
    ) -> None:
        """消费 reply_stream：事件喂桥，终了回存状态；取消/异常/正常三条收尾路由桥承接"""
        run_handle.bind_stream(asyncio.current_task())
        try:
            inputs = Msg(name="user", role="user", content=[TextBlock(text=question)])
            async for event in active.agent.reply_stream(inputs, yield_final_msg=True):
                bridge.on_event(event)
            self._state_store.save(user_id, conversation_id, active.agent.state)
            bridge.complete()
        except asyncio.CancelledError:
            self._state_store.save(user_id, conversation_id, active.agent.state)
            bridge.finish_cancelled_stream()
        except Exception as exc:  # noqa: BLE001 上游异常 → fail 收尾
            self._state_store.save(user_id, conversation_id, active.agent.state)
            bridge.fail(exc)

    async def stop_task(self, task_id: str) -> None:
        """停止流式任务（对应 Java stopTask；偏离：cancelByUser 属主复核未移植，controller 模块登记）"""
        await self._task_manager.cancel(task_id)
