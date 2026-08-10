# -*- coding: utf-8 -*-
"""
core.llm.callback - 流式响应回调接口（对应 ragent 的 StreamCallback）
"""

from abc import ABC, abstractmethod
from typing import List, Optional

# 导入需要的数据类型（避免循环引用，使用 TYPE_CHECKING）
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .schema import SourceRef, GroundingChunk


class StreamCallback(ABC):
    """
    流式响应回调抽象基类（对应 ragent 的 StreamCallback 接口）

    所有回调方法默认都是异步的，以适配 Python asyncio 生态。
    调用方可根据需要实现部分方法（BaseStreamCallback 提供了默认空实现）。
    """

    # ==================== 生命周期方法 ====================

    @abstractmethod
    async def on_start(self) -> None:
        """
        流式响应开始前的回调（Python 扩展方法，Java 无对应）。

        在发起 HTTP 请求前调用，适合：
            - 初始化计时器（记录首字延迟）
            - 打开数据库连接
            - 初始化 WebSocket 消息
        """
        pass

    # ==================== 核心内容方法 ====================

    @abstractmethod
    async def on_content(self, token: str) -> None:
        """
        接收一次增量内容（对应 Java 的 onContent）

        Args:
            token: 当前推送的增量文本片段
        """
        pass

    @abstractmethod
    async def on_thinking(self, token: str) -> None:
        """
        接收思考过程增量内容（对应 Java 的 onThinking）
        默认空实现，未支持思考的场景可以忽略

        Args:
            token: 当前推送的思考内容片段
        """
        pass

    # ==================== 元数据方法（新增，对应 Java default 方法） ====================

    async def on_reply_to_message_id(self, message_id: str) -> None:
        """
        记录当前回答对应的用户消息 ID（对应 Java 的 onReplyToMessageId）

        在流式开始前调用，用于关联用户问题和 AI 回答。

        Args:
            message_id: 用户消息 ID
        """
        pass

    async def on_sources(self, sources: List["SourceRef"]) -> None:
        """
        接收回答来源（文档级），对应 Java 的 onSources

        检索完成后回调一次，由实现方暂存，随完成事件一并下发。
        用于前端展示"答案来自哪些文档"。

        Args:
            sources: 文档级来源列表
        """
        pass

    async def on_grounding_chunks(self, chunks: List["GroundingChunk"]) -> None:
        """
        接收推荐问题 grounding 片段，对应 Java 的 onGroundingChunks

        检索完成后回调一次，由实现方暂存，随 assistant 消息一并落库。
        用于后续推荐追问生成 grounding。

        Args:
            chunks: grounding 片段列表
        """
        pass

    # ==================== 结束/异常方法 ====================

    @abstractmethod
    async def on_complete(self) -> None:
        """
        整个推理流程结束（对应 Java 的 onComplete）
        在所有 onContent 调用完成后触发。
        """
        pass

    @abstractmethod
    async def on_error(self, error: Exception) -> None:
        """
        流式推送过程中出现异常（对应 Java 的 onError）

        Args:
            error: 异常对象
        """
        pass


# ==================== 便捷基类（默认空实现） ====================

class BaseStreamCallback(StreamCallback):
    """
    简化版的 StreamCallback 基类，提供所有方法的默认空实现。
    子类只需重写需要处理的方法。
    """

    async def on_start(self) -> None:
        pass

    async def on_content(self, token: str) -> None:
        pass

    async def on_thinking(self, token: str) -> None:
        pass

    async def on_reply_to_message_id(self, message_id: str) -> None:
        pass

    async def on_sources(self, sources: List["SourceRef"]) -> None:
        pass

    async def on_grounding_chunks(self, chunks: List["GroundingChunk"]) -> None:
        pass

    async def on_complete(self) -> None:
        pass

    async def on_error(self, error: Exception) -> None:
        print(f"[StreamCallback Error] {error}")