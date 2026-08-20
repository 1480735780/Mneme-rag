# -*- coding: utf-8 -*-
"""
rag.service.stream - 流式问答核心（对应 ragent bootstrap rag.service.handler + dto + enums）

M3 流式问答核心子包：
    - protocol：SSE 事件协议模型（SSEEventType + MetaPayload/MessageDelta/CompletionPayload），
      对齐 Java rag.dto + rag.enums.SSEEventType；
    - event_handler：StreamChatEventHandler（SSE 下发 + 消息落库 + 取消补偿），对齐 Java StreamChatEventHandler；
    - callback_factory：StreamCallbackFactory；
    - task_manager：StreamTaskManager（本地注册表 + Redis 取消标记/pubsub + 协程取消）；
    - trace_runner：RagTraceContext + 入口追踪包装（StreamChatTraceRunner）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.handler.*
    - com.nageoffer.ai.ragent.rag.dto.*
    - com.nageoffer.ai.ragent.rag.enums.SSEEventType
"""