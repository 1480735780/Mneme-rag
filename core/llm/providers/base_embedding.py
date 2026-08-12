# -*- coding: utf-8 -*-
"""
📌 这个文件是干什么的？
    它定义了所有 Embedding 客户端（Qwen / OpenAI / SiliconFlow）都必须遵守的“契约”。
    它不包含任何 HTTP 请求或业务逻辑，只规定了“客户端必须实现哪些方法”。

📌 它在整个 Embedding 流程中处于什么位置？

    ┌─────────────────────────────────────────────────────┐
    │  业务层（RAG Pipeline）                            │
    │  └── 调用 embedding_service.embed("文本")          │
    └─────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  RoutingEmbeddingService（路由服务）               │
    │  ├── 1. 通过 ModelSelector 选候选模型             │
    │  ├── 2. 通过 RoutingExecutor 故障转移             │
    │  └── 3. 调用 client.embed(...)                    │
    └─────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  🔵 BaseEmbeddingClient（【本文件】）              │
    │  ├── 定义 embed() 方法签名                        │
    │  ├── 定义 embed_batch() 方法签名                  │
    │  └── 定义 provider 属性                           │
    │  ⚠️  这是一个纯抽象类，没有任何实现！             │
    └─────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │ QwenClient  │    │ OpenAIClient│    │SiliconClient│
    │  (真正发    │    │  (真正发    │    │  (真正发    │
    │   HTTP请求) │    │   HTTP请求) │    │   HTTP请求) │
    └─────────────┘    └─────────────┘    └─────────────┘

📌 为什么需要这个文件？
    1. 依赖倒置：RoutingEmbeddingService 只依赖这个抽象类，不依赖具体实现。
    2. 开闭原则：新增 Provider 只需继承本类，无需修改 routing 层代码。
    3. 类型安全：IDE 能自动补全，mypy 能静态检查。

对应 Java 源码：
    com.nageoffer.ai.ragent.infra.embedding.EmbeddingClient.java
"""

from abc import ABC, abstractmethod
from typing import List
from ..model.model_target import ModelTarget


class BaseEmbeddingClient(ABC):
    """文本嵌入客户端抽象接口（对应 Java EmbeddingClient）"""

    @property
    @abstractmethod
    def provider(self) -> str:
        """
        获取嵌入服务提供商名称（对应 Java provider()）

        Returns:
            str: 提供商标识字符串，如 "qwen", "openai", "siliconflow"
        """
        pass

    @abstractmethod
    async def embed(self, text: str, target: ModelTarget) -> List[float]:
        """
        将单个文本转换为嵌入向量（对应 Java embed(String text, ModelTarget target)）

        Args:
            text: 待嵌入的文本内容
            target: 目标模型配置（含提供商、模型名、API Key 等）

        Returns:
            List[float]: 文本的向量表示
        """
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str], target: ModelTarget) -> List[List[float]]:
        """
        批量将多个文本转换为嵌入向量（对应 Java embedBatch(List<String> texts, ModelTarget target)）

        Args:
            texts: 待嵌入的文本列表
            target: 目标模型配置

        Returns:
            List[List[float]]: 文本向量列表，每个文本对应一个向量
        """
        pass