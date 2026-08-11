# -*- coding: utf-8 -*-
"""
core.llm.enums - AI 模型领域枚举（对应 ragent 的 infra/enums 包）

本模块统一管理模型基础设施中散落的字符串常量，对齐 ragent 的三个枚举：
    - Tier            <-> infra/enums/Tier.java
    - ModelProvider   <-> infra/enums/ModelProvider.java
    - ModelCapability <-> infra/enums/ModelCapability.java

设计说明：
    - 每个枚举承载其"外部字符串值"（如档位键、provider 标识、能力显示名）；
    - 提供便捷属性/方法（key / id / display_name / matches），避免各层各自
      from_string 的重复实现；
    - 后续各层（selector / executor / RoutingLLMService）逐步改用本枚举，
      替换散落字符串，保证路由正确性的单一事实来源。

命名偏差说明：
    - Java 的 ModelProvider.BAI_LIAN.id = "bailian"；
      mneme-rag 当前 ai.yaml 与 providers/qwen.py 使用 "qwen"。
      此处保留与 Java 一致的两者，实际取值以系统配置为准（见计划 P1-1 备注）。
"""

from enum import Enum
from typing import Optional


class Tier(Enum):
    """
    模型档位枚举（对应 Java 的 Tier）。

    档位表达「质量 / 成本 / 时延预算」，非业务任务本身。默认档为 standard，
    调用点想要更快/更强的模型时显式传入本枚举覆盖，路由层据此在对应档位内选候选。

    每个成员的值（value）对应 ai.yaml 中 ai.chat.tiers 下的档位键。
    """

    FAST = "fast"          # 快速档：低延迟优先，用于高频或低风险任务
    STANDARD = "standard"  # 标准档：质量与成本平衡，未显式指定档位时的默认档
    DEEP = "deep"          # 深度档：高质量、高成本，用于深度思考回答（thinking=true 触发）

    @property
    def key(self) -> str:
        """档位键（对应 Java 的 getKey()），即 ai.chat.tiers 下的键。"""
        return self.value

    @classmethod
    def from_key(cls, key: str) -> Optional["Tier"]:
        """按档位键反查枚举；未匹配返回 None（避免抛错导致调用点崩溃）。"""
        if not key:
            return None
        for tier in cls:
            if tier.value == key:
                return tier
        return None


class ModelProvider(Enum):
    """
    模型提供商枚举（对应 Java 的 ModelProvider）。

    统一管理提供商名称，避免散落的字符串常量。
    每个成员的值（value）对应 ai.yaml 中 providers 下的 key。
    """

    OLLAMA = "ollama"              # Ollama 本地模型服务
    BAI_LIAN = "bailian"           # 阿里云百炼大模型平台（mneme-rag 当前用 "qwen"）
    SILICON_FLOW = "siliconflow"   # 硅基流动 AI 模型服务
    AI_HUB_MIX = "aihubmix"        # 推理时代 AI 模型服务
    NOOP = "noop"                  # 空实现，用于测试或占位

    @property
    def id(self) -> str:
        """提供商标识（对应 Java 的 getId()），即 providers 下的 key。"""
        return self.value

    def matches(self, provider: Optional[str]) -> bool:
        """
        忽略大小写匹配提供商字符串（对应 Java 的 matches()）。

        Args:
            provider: 待匹配的提供商字符串，可为 None。

        Returns:
            bool: provider 与当前枚举值忽略大小写相等时为 True。
        """
        return provider is not None and provider.lower() == self.value


class ModelCapability(Enum):
    """
    模型能力枚举（对应 Java 的 ModelCapability）。

    定义 AI 模型支持的各种能力类型，用于路由链路区分不同能力组。
    每个成员的值（value）为能力的显示名称，用于错误与日志文案。
    """

    CHAT = "Chat"          # 聊天对话能力
    EMBEDDING = "Embedding"  # 向量嵌入能力
    RERANK = "Rerank"      # 重排序能力

    @property
    def display_name(self) -> str:
        """能力显示名（对应 Java 的 getDisplayName()），用于日志/错误文案。"""
        return self.value
