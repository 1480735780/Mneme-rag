# -*- coding: utf-8 -*-
"""
rag.service.conversation_service - 会话服务（对应 Java ConversationServiceImpl + ConversationTitleGenerator）

会话域在线服务：列表 / 创建或更新（新会话触发 LLM 标题生成）/ 重命名 / 删除（级联软删）。

对齐 Java 语义：
    - listByUserId：空用户返回空列表；全量（last_time 倒序）——Java 无分页；
    - createOrUpdate：新会话 → 标题生成（TitleGenerator）→ 插入；已存在 → **仅刷新 last_time**
      （不重新生成标题）；
    - rename：title 非空校验 + 长度上限（title_max_length，超长抛 ClientException）+ trim，
      **不刷新 last_time**（最近时间由消息落库路径维护）；
    - delete：校验归属（不存在抛 ClientException）→ **级联软删 会话 + 消息 + 摘要 三表**
      （对齐 Java @Transactional 三 mapper 级联；Python 无跨语句事务，串行三步，
      InMemory 经 RLock 原子，SQL 后端接受短暂中间态——已知边界）；
    - ConversationTitleGenerator：模板渲染（title_max_chars/question 槽位）→
      ChatRequest(temperature=0.7, topP=0.3, thinking=False) → llm.chat(tier=FAST)，
      异常兜底「新对话」；title_max_length<=0 回落 30。独立类对齐 Java 独立 bean
      （Java 为 AOP trace 生效拆类；Python 保留结构对齐 + 可测性）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationServiceImpl
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationTitleGenerator
    - com.nageoffer.ai.ragent.rag.service.bo.ConversationCreateBO
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from common.exception.business import ClientException
from core.llm.chat import LLMService
from core.llm.enums import Tier
from core.llm.schema import ChatRequest, Message
from rag.dao.conversation_dao import ConversationDao
from rag.dao.message_dao import MessageDao
from rag.dao.summary_dao import ConversationSummaryDao
from rag.dao.support import now_iso
from rag.memory.config import MemoryProperties
from rag.prompt.formatter import PromptTemplateLoader

logger = logging.getLogger(__name__)

# 标题生成模板路径（对应 Java RAGConstant.CONVERSATION_TITLE_PROMPT_PATH）
CONVERSATION_TITLE_PROMPT_PATH = "conversation-title.st"

# 标题长度非法时回落值（对齐 Java ConversationTitleGenerator 的 maxLen<=0 兜底）
_TITLE_MAX_CHARS_FALLBACK = 30

# 标题生成失败兜底文案（对齐 Java catch Exception → "新对话"）
_TITLE_FALLBACK = "新对话"


def _utf16_length(text: str) -> int:
    """按 UTF-16 码元计数（对齐 Java String.length()：非 BMP 字符如 emoji 计 2）"""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


class ConversationTitleGenerator:
    """
    会话标题生成器（对应 Java ConversationTitleGenerator）

    拆为独立类对齐 Java 独立 bean（Java 为让 @RagTraceNode AOP 生效；
    Python 保留结构对齐与可测性）。generate 为 async（LLMService.chat 是 async 接口）。
    """

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        template_loader: Optional[PromptTemplateLoader] = None,
        properties: Optional[MemoryProperties] = None,
    ):
        self._llm_service = llm_service
        self._template_loader = template_loader or PromptTemplateLoader()
        self._properties = properties or MemoryProperties()

    async def generate(self, question: str) -> str:
        """
        生成会话标题：模板渲染 → LLM FAST 档；异常兜底「新对话」（对齐 Java generate）

        Returns:
            标题文本；LLM 失败返回「新对话」
        """
        max_len = self._properties.title_max_length
        if max_len <= 0:
            max_len = _TITLE_MAX_CHARS_FALLBACK
        prompt = self._template_loader.render(
            CONVERSATION_TITLE_PROMPT_PATH,
            {"title_max_chars": str(max_len), "question": question or ""},
        )
        request = ChatRequest(
            messages=[Message.user(prompt)],
            temperature=0.7,
            topP=0.3,
            thinking=False,
        )
        if self._llm_service is None:
            # 未注入 LLM（M3 之前装配骨架）：直接回退默认标题，不发出请求
            return _TITLE_FALLBACK
        try:
            return await self._llm_service.chat(request, tier=Tier.FAST)
        except Exception as ex:  # noqa: BLE001 —— 对齐 Java catch Exception 兜底
            logger.warning("生成会话标题失败: %s", ex)
            return _TITLE_FALLBACK


class ConversationService:
    """
    会话服务（对应 Java ConversationServiceImpl）

    组合会话/消息/摘要 dao 与标题生成器，承载会话域全部业务用例。
    create_or_update 为 async（标题生成走 LLM）；list/rename/delete 为同步。
    """

    def __init__(
        self,
        conversation_dao: ConversationDao,
        message_dao: MessageDao,
        summary_dao: ConversationSummaryDao,
        title_generator: ConversationTitleGenerator,
        properties: Optional[MemoryProperties] = None,
    ):
        self._conversation_dao = conversation_dao
        self._message_dao = message_dao
        self._summary_dao = summary_dao
        self._title_generator = title_generator
        self._properties = properties or MemoryProperties()

    def list_by_user(self, user_id: Optional[str]) -> List[Dict]:
        """
        按用户列会话（last_time 倒序全量，对齐 Java listByUserId——Java 无分页）

        空 / 纯空白 user_id 返回空列表。行含 conversation_id/title/last_time，
        VO 组装由 controller 边界承担。
        """
        if not user_id or not str(user_id).strip():
            return []
        return self._conversation_dao.list_by_user(user_id)

    async def create_or_update(
        self,
        conversation_id: str,
        user_id: str,
        question: str,
        last_time: Optional[str] = None,
    ) -> None:
        """
        创建或更新会话（对应 Java createOrUpdate）：
            - 新会话：LLM 生成标题 → 插入（conversation_id/user_id/title/last_time）；
            - 已存在：**仅刷新 last_time**，不重新生成标题。

        Args:
            conversation_id: 会话 ID
            user_id:         用户 ID（空白抛 ClientException「用户信息缺失」）
            question:        用户问题（新会话标题生成输入）
            last_time:       最近时间（通常为消息落库时间；缺省取当前时间）
        """
        if not user_id or not str(user_id).strip():
            raise ClientException("用户信息缺失")
        effective_last_time = last_time or now_iso()

        existing = self._conversation_dao.find_by_conversation_id(conversation_id, user_id)
        if existing is None:
            title = await self._title_generator.generate(question)
            self._conversation_dao.insert_conversation(
                conversation_id=conversation_id,
                user_id=user_id,
                title=title,
                last_time=effective_last_time,
            )
            return
        self._conversation_dao.refresh_last_time(conversation_id, user_id, effective_last_time)

    def rename(self, conversation_id: str, user_id: str, title: str) -> None:
        """
        重命名会话（对应 Java rename）：
            - title 空白 → ClientException「会话名称不能为空」；
            - 超过 title_max_length → ClientException；
            - 会话不存在 / 归属不符 → ClientException「会话不存在」；
            - 正常路径：trim 后仅更新 title（不刷 last_time）。
        """
        if not title or not str(title).strip():
            raise ClientException("会话名称不能为空")
        trimmed = str(title).strip()
        max_len = self._properties.title_max_length
        # 按 UTF-16 码元计数（对齐 Java title.length()，非 BMP 字符如 emoji 计 2）
        if _utf16_length(trimmed) > max_len:
            raise ClientException(f"会话名称长度不能超过{max_len}个字符")
        if not self._conversation_dao.rename(conversation_id, user_id, trimmed):
            raise ClientException("会话不存在")

    def delete(self, conversation_id: str, user_id: str) -> None:
        """
        删除会话（对应 Java @Transactional delete）：级联软删 会话 + 消息 + 摘要 三表

        会话不存在 / 归属不符 → ClientException「会话不存在」。
        三步串行非跨语句原子（已知边界：InMemory 原子 / SQL 接受短暂中间态）。
        """
        if not self._conversation_dao.soft_delete(conversation_id, user_id):
            raise ClientException("会话不存在")
        self._message_dao.soft_delete_by_conversation(conversation_id, user_id)
        self._summary_dao.soft_delete_by_conversation(conversation_id, user_id)