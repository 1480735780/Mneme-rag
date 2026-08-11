```
ragent-study/infra-ai/chat/

ChatClient.java
        |
        ↓
mneme-rag/core/llm/base.py
(BaseChatClient)

------------------------------------------------

AbstractOpenAIStyleChatClient.java
        |
        ↓
mneme-rag/core/llm/providers/openai_style.py

------------------------------------------------

BaiLianChatClient.java
        |
        ↓
mneme-rag/core/llm/providers/qwen.py

------------------------------------------------

OllamaChatClient.java
        |
        ↓
mneme-rag/core/llm/providers/ollama.py

------------------------------------------------

OpenAIStyleChatClient实现
        |
        ↓
openai.py