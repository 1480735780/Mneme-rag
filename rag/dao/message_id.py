# -*- coding: utf-8 -*-
"""
rag.dao.message_id - 会话消息主键生成（单一来源）

收敛引擎记忆路径（store.append）与在线写消息（message_service.add_message）的 ID 生成
为**模块级单一原子计数器**，消除「两套独立 itertools.count 同毫秒碰撞主键」缺陷
（对齐 Java：JdbcConversationMemoryStore.append 复用 ConversationMessageService.addMessage，
Java 仅单一 ID 来源）。

格式：毫秒时间戳 + 6 位序号（对齐既有 store._next_message_id，数字串可参与 ID 窗口比较）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationMessageServiceImpl#addMessage
"""
from __future__ import annotations

import itertools
import time

# 模块级原子序号（CPython itertools.count.__next__ 原子，全局唯一、跨实例共享）
_SEQ_COUNTER = itertools.count()


def next_message_id() -> str:
    """生成消息主键：毫秒时间戳 + 全局 6 位序号（进程内唯一，多入口共享单一计数器）"""
    return f"{int(time.time() * 1000)}{next(_SEQ_COUNTER):06d}"