"""
知识图谱检索配置（对应 ragent GraphProperties）

type=none（默认）时不注册任何图谱读写实现（检索通道与写入同步装饰器均不织入），
与「从未引入图谱检索」运行期等价。

与关键词检索对称：此处管后端类型与连接（对应 rag.keyword），
通道行为（启用/范围/倍数）放在检索通道配置（SearchChannelProperties 的 channels.graph）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.config.GraphProperties
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LightRagProperties:
    """
    LightRAG 微服务连接配置（对应 Java GraphProperties.LightRag）

    Attributes:
        base_url:   LightRAG server 基址
        api_key:    API Key（对应 LightRAG 的 X-API-Key 头）；本地部署默认留空、不发送该头
        query_mode: 查询模式：naive / local / global / hybrid / mix
        timeout_ms: 请求超时（毫秒）
    """

    base_url: str = "http://127.0.0.1:9621"
    api_key: str = ""
    query_mode: str = "mix"
    timeout_ms: int = 30000


@dataclass
class GraphProperties:
    """
    知识图谱检索配置（对应 Java GraphProperties）

    Attributes:
        type:            图谱检索后端类型，可选 none（关闭）/ lightrag
        lightrag:        LightRAG 微服务连接配置
        embedding_model: 图谱侧 embedding 模型标识（独立于各知识库的向量 embedding，首次索引后不可更换）
    """

    type: str = "none"
    lightrag: LightRagProperties = field(default_factory=LightRagProperties)
    embedding_model: str = ""

    def is_lightrag(self) -> bool:
        """是否启用 lightrag 后端（对应 Java isLightrag）"""
        return self.type.lower() == "lightrag"
