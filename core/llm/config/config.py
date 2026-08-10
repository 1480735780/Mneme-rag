"""
core.llm.config - AI 模型配置管理

本模块定义了 Mneme-rag 运行时所需的全部模型配置结构，包括：
    - 提供商配置（API Key、Base URL、端点映射）
    - 模型组配置（chat / embedding / rerank / vlm）
    - 模型候选注册（id、provider、model、url、dimension 等）
    - 档位策略（fast / standard / deep 等，用于 chat）
    - 故障转移与熔断参数
    - 流式响应参数

对应 ragent 源码：
    com.nageoffer.ai.ragent.infra.config.AIModelProperties

设计原则：
    1. 结构化配置：所有配置以 dataclass 嵌套定义，与 Java 的静态内部类一一对应。
    2. YAML 驱动：通过 load_config() 方法从 YAML 文件加载，保持与 Spring Boot
       application.yml 类似的配置体验。
    3. 类型安全：每个字段都有明确的类型注解，便于 IDE 自动补全和静态检查。
    4. 向后兼容：所有字段都提供合理的默认值，支持渐进式配置。

配置示例（对应 YAML）：
    ai:
      providers:
        qwen:
          url: https://dashscope.aliyuncs.com
          api_key: ${QWEN_API_KEY}
          endpoints:
            chat: /compatible-mode/v1/chat/completions
            embedding: /compatible-mode/v1/embeddings
      chat:
        default_tier: standard
        deep_thinking_tier: deep
        candidates:
          - id: qwen-max
            provider: qwen
            model: qwen-max
            enabled: true
            supports_thinking: true
          - id: qwen-turbo
            provider: qwen
            model: qwen-turbo
            enabled: true
        tiers:
          fast:
            candidates: [qwen-turbo]
            timeout_ms: 5000
          standard:
            candidates: [qwen-max]
            timeout_ms: 30000
          deep:
            candidates: [qwen-max]
            timeout_ms: 60000
      selection:
        failure_threshold: 2
        open_duration_ms: 30000
      stream:
        message_chunk_size: 5
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import logging
import os
import yaml

logger = logging.getLogger(__name__)

@dataclass
class ProviderConfig:
    """
    AI提供商配置
    
    定义单个 AI 提供商（如 OpenAI、Qwen、Ollama）的基本连接信息。
    """
    url: str                           # 提供商基础 URL
    api_key: Optional[str] = None      # API 密钥（可从环境变量注入）
    endpoints: Dict[str, str] = field(default_factory=dict)  # 端点映射（chat/embedding/rerank）

    #定义解析提供商配置信息的方法

    def resolve_api_key(self) -> str:
        if not self.api_key:
            return ""
        
        value = self.api_key.strip()
        
        # 检测 ${ENV_VAR} 格式
        if value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]  # 提取变量名，如 "QWEN_API_KEY"
            # 从环境变量读取，如果不存在则告警并返回空字符串
            resolved = os.environ.get(env_var)
            if resolved is None:
                logger.warning(
                    "环境变量 %s 未设置，API Key 解析为空（provider 配置中的占位符无法解析）",
                    env_var,
                )
                return ""
            return resolved
        
        # 如果不是占位符格式，直接返回原值（兼容明文配置，不推荐）
        return value


@dataclass
class ModelCandidate:
    """
    模型候选配置（对应 Java 的 ModelCandidate）
    
    在模型组中注册一个具体的模型实例，包含其提供商、模型名、启用状态等。
    """

    id: str   #模型唯一标识符
    provider: str #模型提供商名称
    model: str #模型名称
    url: Optional[str] = None  #模型访问 URL（可选，缺省时由 ProviderConfig.url + endpoints 解析）
    dimension: Optional[int] = None    # 向量维度（仅 embedding 模型使用）
    priority: int = 100                # 优先级（数值越小优先级越高）
    enabled: bool = True               # 是否启用该模型
    supports_thinking: bool = False    # 是否支持思考链（DeepSeek-R1 / QwQ）


@dataclass
class TierConfig:
    """
    档位配置（对应 Java 的 TierConfig）
    
    定义单个档位（如 fast / standard / deep）的候选模型列表和超时预算。
    仅用于 chat 模型组。
    """
    candidates: List[str] = field(default_factory=list)  # 有序候选模型 ID 列表
    timeout_ms: Optional[int] = None                     # 该档位的超时预算（毫秒）


@dataclass
class ModelGroup:
    """
    模型组配置（对应 Java 的 ModelGroup）
    
    定义一类能力（chat / embedding / rerank / vlm）的候选模型集和选择策略。
    """

    default_model: Optional[str] = None  # 默认模型 ID（embedding/rerank/vlm 使用）
    candidates: List[ModelCandidate] = field(default_factory=list)  # 候选模型列表    
    default_tier: Optional[str] = None       # 默认档位名（仅 chat 使用）
    deep_thinking_tier: Optional[str] = None # 深度思考档位名（仅 chat 使用）用户开启深度思考时的目标档位
    tiers: Dict[str, TierConfig] = field(default_factory=dict)  # 档位配置（仅 chat 使用）key: 档位名（如 fast/standard/deep），value: 该档位的候选与超时

@dataclass
class SelectionConfig:
    """
    模型选择策略配置（对应 Java 的 Selection）
    
    故障转移和熔断策略，用于高可用场景。
    """
    failure_threshold: int = 2          # 失败阈值，超过后触发熔断
    open_duration_ms: int = 30000       # 熔断器打开持续时间（毫秒）

@dataclass
class StreamConfig:
    """
    流式响应配置（对应 Java 的 Stream）
    
    控制流式输出的行为。
    """
    message_chunk_size: int = 5         # 消息分块大小（用于前端流式展示）

@dataclass
class AIModelConfig:
    """
    AI 模型总配置（对应 Java 的 AIModelProperties）
    
    包含所有提供商配置、模型组配置、选择策略和流式配置。
    这是从 YAML 文件加载后的顶层对象。
    """
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)
    chat: ModelGroup = field(default_factory=ModelGroup)
    embedding: ModelGroup = field(default_factory=ModelGroup)
    rerank: ModelGroup = field(default_factory=ModelGroup)
    vlm: ModelGroup = field(default_factory=ModelGroup)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)




# ==================== 配置加载器（YAML 解析） ====================

def load_config_from_dict(data: Dict[str, Any]) -> AIModelConfig:
    """
    从字典加载 AI 模型配置（用于从 YAML 解析后转换）。
    
    Args:
        data: 解析 YAML 后得到的字典对象，顶层 key 为 "ai"。
    
    Returns:
        AIModelConfig: 结构化的配置对象。
    """
    ai_data = data.get("ai", {})
    
    # 解析 providers
    providers = {}
    for name, cfg in ai_data.get("providers", {}).items():
        providers[name] = ProviderConfig(
            url=cfg.get("url", ""),
            api_key=cfg.get("api_key"),
            endpoints=cfg.get("endpoints", {})
        )
    
    # 解析 model groups（chat / embedding / rerank / vlm）
    def parse_model_group(group_data: Dict[str, Any]) -> ModelGroup:
        candidates = []
        for cand in group_data.get("candidates", []):
            candidates.append(ModelCandidate(
                id=cand.get("id", ""),
                provider=cand.get("provider", ""),
                model=cand.get("model", ""),
                url=cand.get("url"),
                dimension=cand.get("dimension"),
                priority=cand.get("priority", 100),
                enabled=cand.get("enabled", True),
                supports_thinking=cand.get("supports_thinking", False)
            ))
        tiers = {}
        for tier_name, tier_cfg in group_data.get("tiers", {}).items():
            tiers[tier_name] = TierConfig(
                candidates=tier_cfg.get("candidates", []),
                timeout_ms=tier_cfg.get("timeout_ms")
            )
        return ModelGroup(
            candidates=candidates,
            default_model=group_data.get("default_model"),
            default_tier=group_data.get("default_tier"),
            deep_thinking_tier=group_data.get("deep_thinking_tier"),
            tiers=tiers
        )
    
    chat = parse_model_group(ai_data.get("chat", {}))
    embedding = parse_model_group(ai_data.get("embedding", {}))
    rerank = parse_model_group(ai_data.get("rerank", {}))
    vlm = parse_model_group(ai_data.get("vlm", {}))
    
    # 解析 selection
    sel_data = ai_data.get("selection", {})
    selection = SelectionConfig(
        failure_threshold=sel_data.get("failure_threshold", 2),
        open_duration_ms=sel_data.get("open_duration_ms", 30000)
    )
    
    # 解析 stream
    stream_data = ai_data.get("stream", {})
    stream = StreamConfig(
        message_chunk_size=stream_data.get("message_chunk_size", 5)
    )
    
    return AIModelConfig(
        providers=providers,
        chat=chat,
        embedding=embedding,
        rerank=rerank,
        vlm=vlm,
        selection=selection,
        stream=stream
    )


def load_config_from_yaml(file_path: str) -> AIModelConfig:
    """
    从 YAML 文件加载 AI 模型配置。
    
    Args:
        file_path: YAML 配置文件路径（如 "config/ai.yaml"）。
    
    Returns:
        AIModelConfig: 结构化的配置对象。
    
    Raises:
        FileNotFoundError: 配置文件不存在。
        yaml.YAMLError: YAML 解析错误。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return load_config_from_dict(data)


def get_provider_config(config: AIModelConfig, provider_name: str) -> ProviderConfig:
    """
    从配置中获取指定提供商的配置。
    
    Args:
        config: AIModelConfig 对象。
        provider_name: 提供商名称（如 "qwen"）。
    
    Returns:
        ProviderConfig: 提供商配置。
    
    Raises:
        KeyError: 如果提供商未注册。
    """
    if provider_name not in config.providers:
        raise KeyError(
            f"未注册的提供商: {provider_name}。"
            f"已注册: {list(config.providers.keys())}"
        )
    return config.providers[provider_name]


def resolve_model_endpoint(
    config: AIModelConfig,
    provider_name: str,
    capability: str  # "chat" / "embedding" / "rerank"
) -> str:
    """
    解析模型端点的完整 URL。
    
    优先使用 ModelCandidate 中的 url，否则使用 ProviderConfig.url + endpoints[capability]。
    
    Args:
        config: AIModelConfig 对象。
        provider_name: 提供商名称。
        capability: 能力类型（"chat" / "embedding" / "rerank"）。
    
    Returns:
        str: 完整的端点 URL。
    
    Raises:
        KeyError: 如果端点未配置。
    """
    provider = get_provider_config(config, provider_name)
    base_url = provider.url.rstrip("/")
    endpoint = provider.endpoints.get(capability)
    if not endpoint:
        raise KeyError(f"提供商 {provider_name} 未配置 {capability} 端点")
    return f"{base_url}{endpoint}"