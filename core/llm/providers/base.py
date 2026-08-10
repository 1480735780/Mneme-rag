# -*- coding: utf-8 -*-
"""
core.llm.providers.base - AI 模型客户端抽象协议（对应 ragent 的 ChatClient 接口）

本模块定义了 Mneme-rag 与底层大模型提供商交互的顶层抽象契约。
所有具体的模型客户端（OpenAI、Qwen、Ollama 等）都必须实现本接口，
以确保它们在 Model Management Layer 中可被统一调度和替换。

架构对应关系：
    Ragent (Java)                Mneme-rag (Python)
    ──────────────────────────────────────────────────
    ChatClient (interface)   -->  providers/base.py (BaseChatClient)
    AbstractOpenAIStyleChatClient --> providers/openai_style.py
    QwenClient               -->  providers/qwen.py

设计原则（对齐 ragent ChatClient）：
    1.  接口隔离：只定义"能做什么"（chat / stream_chat），不定义"怎么做"
        （HTTP 实现、JSON 序列化等由子类负责）。
    2.  运行时识别：通过 provider 属性，使路由层能够在运行时动态匹配
        当前客户端实例与 ModelTarget.provider。
    3.  流式优先：明确区分同步完整返回（chat）和流式增量返回（stream_chat），
        以适配大模型生成的延迟特性。
    4.  异步原生：充分利用 Python async/await，与 httpx/aiohttp 等
        非阻塞 HTTP 客户端无缝协作。

使用规范：
    - 业务层（RAG Pipeline）不应直接依赖本接口的具体实现，而应通过
      core/llm/chat.py 中的 ChatService 门面进行调用。
    - 本接口的实现类应注册到 ChatService 的 clients 字典中，
      以 provider 属性值作为键名。
"""

from abc import ABC, abstractmethod

from ..schema import ChatRequest
from core.llm.model.model_target import ModelTarget
from ..callback import StreamCallback


class BaseChatClient(ABC):
    """
    AI 对话模型客户端抽象基类。

    定义了与任何大模型提供商进行对话交互的统一协议。所有具体实现
    （如 QwenClient、OpenAIClient、OllamaClient）都必须继承本类
    并实现所有抽象方法。

    生命周期约定：
        - 每个实例通常代表一个"提供商集群"（如所有 Qwen 模型共享一个
          QwenClient 实例），而具体的模型版本（qwen-max vs qwen-turbo）
          通过 ModelTarget.model 参数动态指定。
        - 实例应由工厂或 DI 容器管理，持有必要的配置（API Key、Base URL 等）。

    线程安全：
        由于 Python asyncio 的并发特性，实现类应确保实例级别的
        HTTP 连接池（如 httpx.AsyncClient）是线程/任务安全的。
    """

    # ==================== 抽象属性（Provider 识别） ====================

    @property
    @abstractmethod
    def provider(self) -> str:
        """
        返回当前客户端的供应商标识符。

        该属性用于路由层在运行时进行精准匹配：
            当 ChatService 收到一个指定 provider="qwen" 的请求时，
            它会遍历所有已注册的 BaseChatClient 实例，找到
            client.provider == "qwen" 的那个，然后将请求转发给它。

        对应 ragent 源码：
            String provider();

        Returns:
            str: 全小写的供应商名称（如 "openai"、"qwen"、"ollama"）。
                 该值必须与 ModelTarget.provider 严格区分大小写匹配。
        """
        pass

    # ==================== 抽象方法（同步/异步生成） ====================

    @abstractmethod
    async def chat(
        self,
        request: "ChatRequest",
        target: "ModelTarget"
    ) -> str:
        """
        非流式对话生成接口。

        语义："等待完整响应返回"。
        尽管方法标记为 async，但其行为是等待模型生成全部内容后一次性返回。
        适用于不需要流式展示的离线场景（如 文档摘要生成、批量评估）。

        对应 ragent 源码：
            String chat(ChatRequest request, ModelTarget target);

        Args:
            request: 包含 messages、temperature、max_tokens 等参数的请求对象。
            target:  指定本次调用的具体模型实例（含 provider、model_name、
                     api_key、timeout_ms 等运行时覆盖配置）。

        Returns:
            str: 模型生成的完整文本内容。

        Raises:
            ModelClientException: 网络异常、鉴权失败、模型返回空内容、
                                  HTTP 4xx/5xx 时抛出。
            asyncio.CancelledError: 当上层任务被取消时抛出（如应用关闭）。
        """
        pass

    @abstractmethod
    async def stream_chat(
        self,
        request: "ChatRequest",
        callback: "StreamCallback",
        target: "ModelTarget"
    ) -> None:
        """
        流式（增量）对话生成接口。

        语义："逐 Token 推送"。
        模型生成的内容会通过 callback 的 on_content / on_thinking 方法
        实时推送给调用方，适用于需要"打字机效果"的交互式场景。

        对应 ragent 源码：
            StreamCancellationHandle streamChat(
                ChatRequest request,
                StreamCallback callback,
                ModelTarget target
            );

        取消机制说明（Python vs Java）：
            Java 版本通过返回 StreamCancellationHandle 显式控制取消。
            Python 版本更简洁：调用方通过 asyncio.create_task() 启动本方法后，
            使用 task.cancel() 触发取消，本方法内部需捕获 CancelledError
            并优雅释放底层 HTTP 连接资源。

        典型实现流程：
            1. 构建流式请求（设置 stream=true）。
            2. 使用 httpx.AsyncClient.stream() 发送请求。
            3. 逐行读取 SSE 数据（data: [DONE] 或 data: {...}）。
            4. 解析 JSON 提取 delta.content（内容）和 delta.reasoning_content（思考）。
            5. 分别回调 callback.on_content() 和 callback.on_thinking()。
            6. 遇到 [DONE] 或连接关闭时，回调 callback.on_complete()。

        Args:
            request:  同 chat 方法的请求对象。
            callback: 接收增量 Token 的异步回调接口。
            target:   同 chat 方法的模型目标对象。

        Returns:
            None: 结果通过 callback 异步推送。

        Raises:
            ModelClientException: 流解析失败或 HTTP 错误时抛出。
            asyncio.CancelledError: 当调用方执行 task.cancel() 时抛出，
                                    实现类应捕获并执行清理动作（关闭 response）。
        """
        pass