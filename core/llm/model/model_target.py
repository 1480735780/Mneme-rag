# -*- coding: utf-8 -*-
"""
core.llm.model.model_target - 模型目标配置记录（对应 ragent 的 ModelTarget record）

本模块定义了路由链路上的标准化执行目标，封装一次模型调用所需的
完整路由元数据：模型标识、候选配置、提供商配置与超时预算。

架构对应关系：
    Ragent (Java)                          Mneme-rag (Python)
    ──────────────────────────────────────────────────
    infra/model/ModelTarget.java       --> core/llm/model/model_target.py (ModelTarget)
    AIModelProperties.ModelCandidate   --> core/llm/config/config.py (ModelCandidate)
    AIModelProperties.ProviderConfig   --> core/llm/config/config.py (ProviderConfig)

职责：
    1. 由 ModelSelector 在选择阶段构建（经启用状态与健康度过滤后产出）；
    2. 作为 RoutingExecutor 故障转移循环中的标准化执行目标；
    3. 被 BaseChatClient 的实现类消费，从中解析模型名、端点与超时配置。

注意：
    - provider 字段类型为 ProviderConfig；NOOP 空实现提供商（provider="noop"）
      场景下允许传入 None，与 Java record 允许 provider 为 null 的语义对齐。
    - 本类为不可变数据载体，构建后不应再修改字段。
"""

from dataclasses import dataclass
from typing import Optional

from ..config.config import ModelCandidate, ProviderConfig


@dataclass
class ModelTarget:
    """
    模型目标配置记录（对应 Java 的 ModelTarget record）。

    用于封装 AI 模型的配置信息，包括模型标识、候选模型配置和提供商配置。

    Attributes:
        id: 模型唯一标识符（显式 id 或 "provider::model" 复合键）。
        candidate: 模型候选配置，包含模型的具体参数和设置。
        provider: 提供商配置，包含模型提供商的相关信息；
            NOOP 提供商场景下可为 None。
        timeout_ms: 本次调用的超时预算（毫秒），来自命中的档位配置；
            None 表示不额外限制，走 HTTP 客户端默认。
    """
    id: str
    candidate: ModelCandidate
    provider: ProviderConfig
    timeout_ms: Optional[int] = None
