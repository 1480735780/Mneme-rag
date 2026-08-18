"""
关键词检索配置（对应 ragent KeywordProperties）

type=none（默认）时不注册任何关键词读写实现，与「从未引入关键词检索」运行期等价。
与图谱检索对称：此处管后端类型与连接（对应 rag.graph），
通道行为（启用）放在检索通道配置（SearchChannelProperties 的 channels.keyword）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.config.KeywordProperties
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EsProperties:
    """
    Elasticsearch 配置（对应 Java KeywordProperties.Es）

    Attributes:
        uris:            ES 连接地址
        index:           共享索引名称：所有知识库的关键词数据都写在此索引，按 collection_name 字段过滤
        analyzer:        写入分词器
        search_analyzer: 查询分词器
    """

    uris: str = "http://127.0.0.1:9200"
    index: str = "rag_keyword_store"
    analyzer: str = "ik_max_word"
    search_analyzer: str = "ik_smart"


@dataclass
class KeywordProperties:
    """
    关键词检索配置（对应 Java KeywordProperties）

    Attributes:
        type: 关键词检索后端类型，可选 none（关闭）/ es
        es:   Elasticsearch 配置
    """

    type: str = "none"
    es: EsProperties = field(default_factory=EsProperties)

    def shared_index(self) -> str:
        """
        全部知识库共用的物理索引名称（对应 Java sharedIndex）

        与 Milvus 共享 collection、PG 共享表同构：单索引承载所有知识库，按 collection_name 字段区分。
        """
        return self.es.index

    def is_es(self) -> bool:
        """是否启用 es 后端"""
        return self.type.lower() == "es"
