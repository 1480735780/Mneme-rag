# -*- coding: utf-8 -*-
"""
rag.service - P4 在线服务层（对应 ragent bootstrap rag.service）

按域组织：conversation / message / feedback / recommended / sample / trace /
term_mapping_admin / agent_admin / settings / stream（流式聊天）/ ratelimit（限流）。
service 组合 rag/dao 数据访问与 core/llm 能力，向 rag/controller 提供业务用例。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.*（RAGChatService / Conversation* / Message* 等）
"""