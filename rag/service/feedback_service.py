# -*- coding: utf-8 -*-
"""
rag.service.feedback_service - 消息反馈 service（对应 Java MessageFeedbackService/Impl +
MessageFeedbackEvent + MessageFeedbackConsumer 的进程内等价）

域职责：
    - 反馈提交/取消：校验（userId/messageId/vote in {1,-1}）-> 校验 assistant 消息归属
      （消息存在 + 归属用户 + role=assistant，对齐 loadAssistantMessage）-> 双路 upsert
      （active/cancelled，submit_time 新鲜度覆盖，对齐 doUpsertFeedback）；
    - 异步分发（4.2，D6 进程内异步替代 MQ）：submit_feedback_async / cancel_feedback_async 仅
      构造事件并 dispatch -> controller 提交后 asyncio.create_task(service.submit_by_event(event))；
    - get_user_votes：批量取用户投票 {message_id: vote}（仅未取消、用户隔离）。

事件模型 MessageFeedbackEvent（对齐 Java record）：message_id/user_id/vote/reason/comment/
cancelled/submit_time(ms)。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.MessageFeedbackService
    - com.nageoffer.ai.ragent.rag.service.impl.MessageFeedbackServiceImpl
    - com.nageoffer.ai.ragent.rag.mq.event.MessageFeedbackEvent
    - com.nageoffer.ai.ragent.rag.mq.MessageFeedbackConsumer
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from common.context.user_context import UserContext
from common.exception.business import ClientException
from rag.dao.feedback_dao import MessageFeedbackDao
from rag.dao.message_dao import MessageDao

logger = logging.getLogger(__name__)

# 反馈值（对齐 Java vote：1=点赞，-1=点踩）
VOTE_UP = 1
VOTE_DOWN = -1


@dataclass
class MessageFeedbackRequest:
    """消息反馈请求（对应 Java MessageFeedbackRequest）"""

    vote: Optional[int] = None
    reason: Optional[str] = None
    comment: Optional[str] = None


@dataclass(frozen=True)
class MessageFeedbackEvent:
    """消息反馈事件（对应 Java MessageFeedbackEvent）"""

    message_id: str
    user_id: str
    submit_time: int
    vote: Optional[int] = None
    reason: Optional[str] = None
    comment: Optional[str] = None
    cancelled: bool = False


class MessageFeedbackService:
    """消息反馈服务（对应 Java MessageFeedbackServiceImpl）"""

    def __init__(
        self,
        feedback_dao: MessageFeedbackDao,
        message_dao: MessageDao,
        dispatcher: Optional[Callable[["MessageFeedbackEvent"], None]] = None,
        now_ms: Optional[Callable[[], int]] = None,
    ):
        self._feedback_dao = feedback_dao
        self._message_dao = message_dao
        self._dispatcher = dispatcher  # 注入式异步分发器（缺省 asyncio.create_task）
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    # ==================== 同步提交（内部直接调用） ====================

    def submit_feedback(self, message_id: str, request: MessageFeedbackRequest) -> None:
        """同步提交反馈（对齐 Java submitFeedback）"""
        user_id = self._require_user()
        self._require_message_id(message_id)
        vote = self._validate_vote(request.vote)
        message = self._load_assistant_message(message_id, user_id)
        self._do_upsert_feedback(
            message_id, user_id, message["conversation_id"],
            vote, request.reason, request.comment, self._now_ms(),
        )

    # ==================== 异步提交/取消（构造事件 + 分发，4.2） ====================

    def submit_feedback_async(self, message_id: str, request: MessageFeedbackRequest) -> None:
        """异步提交反馈：仅构造事件并分发（对齐 Java submitFeedbackAsync）"""
        user_id = self._require_user()
        self._require_message_id(message_id)
        vote = self._validate_vote(request.vote)
        self._dispatch(MessageFeedbackEvent(
            message_id=message_id, user_id=user_id, submit_time=self._now_ms(),
            vote=vote, reason=request.reason, comment=request.comment,
        ))

    def cancel_feedback_async(self, message_id: str) -> None:
        """异步取消反馈：仅构造取消事件并分发（对齐 Java cancelFeedbackAsync）"""
        user_id = self._require_user()
        self._require_message_id(message_id)
        self._dispatch(MessageFeedbackEvent(
            message_id=message_id, user_id=user_id, submit_time=self._now_ms(),
            cancelled=True,
        ))

    # ==================== 事件消费者（异步落库，D6 进程内等价 MQ Consumer） ====================

    async def submit_by_event(self, event: MessageFeedbackEvent) -> None:
        """按事件异步持久化反馈（对齐 Java submitFeedbackByEvent）"""
        message_id = event.message_id
        user_id = event.user_id
        if not message_id or not str(message_id).strip():
            raise ClientException("消息ID不能为空")
        if not user_id or not str(user_id).strip():
            raise ClientException("用户ID不能为空")

        if event.cancelled:
            message = self._load_assistant_message(message_id, user_id)
            try:
                self._feedback_dao.upsert_cancelled(
                    message_id, message["conversation_id"], user_id, event.submit_time
                )
            except Exception:  # noqa: BLE001
                logger.exception("取消反馈落库失败，messageId=%s", message_id)
                raise
            return

        vote = self._validate_vote(event.vote)
        message = self._load_assistant_message(message_id, user_id)
        self._do_upsert_feedback(
            message_id, user_id, message["conversation_id"],
            vote, event.reason, event.comment, event.submit_time,
        )

    # ==================== 批量投票查询 ====================

    def get_user_votes(self, user_id: str, message_ids: List[str]) -> Dict[str, int]:
        """查询用户在一批消息上的投票（对齐 Java getUserVotes）"""
        if not user_id or not str(user_id).strip() or not message_ids:
            return {}
        return self._feedback_dao.votes_by_user(user_id, message_ids)

    # ==================== 内部辅助 ====================

    def _dispatch(self, event: MessageFeedbackEvent) -> None:
        """进程内异步分发：缺省 asyncio.create_task(submit_by_event(event))；可注入替换（测试/MQ 等价）"""
        if self._dispatcher is not None:
            self._dispatcher(event)
            return
        task = asyncio.create_task(self.submit_by_event(event))
        # 消费兜底：后台消费异常不冒泡到请求线程（同 MQ 消费框架吞异常）；仅记录日志
        task.add_done_callback(self._on_consume_error)

    @staticmethod
    def _on_consume_error(task: "asyncio.Future") -> None:
        """后台消费任务结束回调：捕获异常留日志（慎重抛，避免 loop exception handler 告警）"""
        try:
            task.result()
        except Exception:  # noqa: BLE001
            logger.exception("反馈事件后台消费失败")

    def _do_upsert_feedback(self, message_id, user_id, conversation_id,
                            vote, reason, comment, submit_time) -> None:
        """双路 upsert（对齐 Java doUpsertFeedback）；重抛 dao 异常前记日志"""
        try:
            self._feedback_dao.upsert_active(
                message_id, conversation_id, user_id, submit_time, vote, reason, comment
            )
        except Exception:  # noqa: BLE001
            logger.exception("反馈落库失败，messageId=%s", message_id)
            raise

    def _load_assistant_message(self, message_id: str, user_id: str) -> dict:
        """校验 assistant 消息归属（对齐 Java loadAssistantMessage）：存在 + 归属用户 + role==assistant"""
        message = self._message_dao.find_by_id(message_id)
        if message is None:
            raise ClientException("消息不存在")
        if message.get("user_id") != user_id:
            raise ClientException("消息不存在")
        if str(message.get("role") or "").lower() != "assistant":
            raise ClientException("仅支持对助手消息反馈")
        return message

    @staticmethod
    def _require_user() -> str:
        user_id = UserContext.get_user_id()
        if not user_id or not str(user_id).strip():
            raise ClientException("未获取到当前登录用户")
        return user_id

    @staticmethod
    def _require_message_id(message_id: str) -> str:
        if not message_id or not str(message_id).strip():
            raise ClientException("消息ID不能为空")
        return message_id

    @staticmethod
    def _validate_vote(vote: Optional[int]) -> int:
        if vote is None:
            raise ClientException("反馈值不能为空")
        if vote != VOTE_UP and vote != VOTE_DOWN:
            raise ClientException("反馈值必须为 1 或 -1")
        return vote