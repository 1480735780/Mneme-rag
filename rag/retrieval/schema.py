# 检索 DTO：RetrieveRequest / RetrievalBudget / SearchContext / SearchChannelResult / SearchChannelType / RetrievalScope
# （对应 ragent RetrieveRequest + RetrievalBudget + SearchContext + SearchChannelResult + SearchChannelType + RetrievalScope）
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from core.llm.schema import RetrievedChunk

@dataclass
class RetrieveRequest:
    """
    向量检索请求参数（对应 Java RetrieveRequest）

    支持：
        - 基础 query + topK
        - 指定 Milvus collectionName（单个或多个）
        - metadata 等值过滤（扩展用）

    Attributes:
        query: 用户自然语言问题 / 查询语句
        top_k: 返回 TopK（对应 Java topK），默认 5
        collection_name: 目标向量集合名称（单集合，兼容旧调用方）
        collection_names: 目标逻辑 Collection 列表（多集合）
        metadata_filters: 元数据等值过滤条件，实现层可以根据 Map 自动拼接 Milvus Expr（AND 连接）。
            - key 为 metadata 字段名，
            - value 为匹配值
    """
    query: str
    top_k: int = 5
    collection_name: Optional[str] = None
    collection_names: Optional[List[str]] = None
    metadata_filters: Optional[Dict[str, Any]] = None

    def get_effective_collection_names(self) -> List[str]:
        """
        新的多 Collection 参数优先，旧的单 Collection 参数用于兼容已有调用方
        业务目标：把调用方传入的“五花八门”的集合参数，统一整理成一个干净、无重复、无空值的列表，告诉下游的向量库：“到底要去哪几张表里查数据”。
        
        获取规范化后的 Collection 名称列表（对应 Java getEffectiveCollectionNames）

        优先级：
            1. 如果 collection_names 非空，使用 collection_names（去重、去空、trim）
            2. 否则回退到 collection_name（若非空）
            3. 返回空列表（表示使用默认 Collection）

        Returns:
            List[str]: 规范化的 Collection 名称列表
        
        """
        normalized = []
        if self.collection_names:  # 多 Collection 参数优先处理
            for name in self.collection_names:
                if name is not None:
                    trimmed = name.strip()
                    if trimmed:
                        normalized.append(trimmed)
        # 去重（保持顺序）
        seen = set()
        unique = []
        for name in normalized:
            if name not in seen:
                seen.add(name)
                unique.append(name)

        if unique:
            return unique

        # 回退到单 collection
        if self.collection_name and self.collection_name.strip():
            return [self.collection_name.strip()]

        return []
    

@dataclass
class RetrievalBudget:
    """
    检索漏斗的三段预算（对应 Java RetrievalBudget）

    一条 retrieve → fuse → rerank → render 链路有三个方向与成本各异、须各自独立的预算。
    显式拆成三段、各阶段只读属于自己的那一段，杜绝「一个 int 三义」：

        - recall_budget:     每通道 fan-out 基数（想大、保召回）
        - candidate_limit:   融合后送 Rerank 的候选池上限（成本天花板）
        - context_top_k:     最终进 LLM 的条数（想小而精，即产品语义的 topK）

    漏斗单调收窄的不变式：recall_budget >= context_top_k 且 candidate_limit >= context_top_k
    由配置侧启动校验兜底。

    三段预算
    recall_budget → candidate_limit → context_top_k。其中，
       - recall_budget：扩大召回，宁滥勿缺
       - candidate_limit：控制rerank成本
       - context_top_k：控制 LLM 上下文质量

    Attributes:
        recall_budget: 每通道召回基数（对应 Java recallBudget）
        candidate_limit: 融合后送 Rerank 的候选池上限（对应 Java candidateLimit）
        context_top_k: 最终进 LLM 的条数（对应 Java contextTopK）
    """
    recall_budget: int = 100
    candidate_limit: int = 30
    context_top_k: int = 10

    @classmethod
    def uniform(cls, k: int) -> "RetrievalBudget":
        """
        三段同值构造（对应 Java uniform(k)）：用于测试或无需区分预算的平凡场景

        Args:
            k: 三段统一值

        Returns:
            RetrievalBudget: 三段均为 k 的预算对象
        """
        return cls(recall_budget=k, candidate_limit=k, context_top_k=k)


class SearchChannelType(Enum):
    """
    检索通道类型枚举（对应 Java SearchChannelType）

    VECTOR:      向量检索 — 按 KB 意图置信度在通道内决定作用域
    KEYWORD:     关键词检索 — 基于全文检索引擎（如 ES）的关键词分词检索
    GRAPH:       知识图谱检索 — 基于实体与关系的图谱召回（预留）
    WEB_SEARCH:  联网检索 — 基于外部 Web 搜索 API 的实时网络召回
    HYBRID:      混合检索 — 结合多种检索策略
    """
    VECTOR = "vector"
    KEYWORD = "keyword"
    GRAPH = "graph"
    WEB_SEARCH = "web_search"
    HYBRID = "hybrid"


@dataclass
class RetrievalScope:
    """
    检索作用域（对应 Java RetrievalScope）

    每个子问题算一次，向量/关键词/图谱共读一份：
    KB 意图足够置信则收窄到命中库（定向），否则退化为全库（全局）。

    定向下 supplement_collections 是未命中库，向量通道用它并行补一路，
    兜住意图判错导致的漏召回。

    Attributes:
        directed:               是否收窄到命中库
        top_score:              KB 意图最高分，仅用于观测与阈值校准
        intents:                命中的 KB 意图列表，定向时非空
        target_collections:     主检索范围：定向为命中库，全局为全部有效库
        supplement_collections: 补充检索范围：全局作用域下恒为空
    """
    directed: bool = False
    top_score: float = 0.0
    intents: List[Any] = field(default_factory=list)
    target_collections: List[str] = field(default_factory=list)
    supplement_collections: List[str] = field(default_factory=list)

    @staticmethod
    def global_scope(top_score: float, active_collections: List[str]) -> "RetrievalScope":
        """
        全局作用域：不收窄，无补充路（对应 Java global()）
        业务目标：分类不可靠，宁可全搜。
        Args:
            top_score: KB 意图最高分
            active_collections: 全部有效知识库列表

        Returns:
            RetrievalScope: 全局作用域实例
        """
        return RetrievalScope(
            directed=False,
            top_score=top_score,
            intents=[],
            target_collections=active_collections,
            supplement_collections=[],
        )


@dataclass
class SearchContext:
    """
    检索上下文（对应 Java SearchContext）

    携带检索所需的所有信息，在多个通道之间传递。

    Attributes:
        original_question:  原始问题
        rewritten_question: 重写后的问题
        sub_questions:      子问题列表
        intents:            意图识别结果（SubQuestionIntent 列表，暂用 dict 占位）
        budget:             检索预算
        retrieval_scope:    检索作用域
        metadata:           扩展元数据
    """
    original_question: str
    rewritten_question: Optional[str] = None
    sub_questions: List[str] = field(default_factory=list)
    intents: List[Any] = field(default_factory=list)
    budget: Optional[RetrievalBudget] = None
    retrieval_scope: Optional[RetrievalScope] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_main_question(self) -> str:
        """
        获取主问题（优先使用重写后的问题，对应 Java getMainQuestion()）

        Returns:
            str: 重写后的问题（若存在），否则返回原始问题
        """
        return self.rewritten_question if self.rewritten_question else self.original_question


@dataclass
class SearchChannelResult:
    """
    检索通道结果（对应 Java SearchChannelResult）

    封装单个通道的检索结果及元信息。

    Attributes:
        channel_type: 通道类型
        channel_name: 通道名称
        chunks:       检索到的 Chunk 列表
        latency_ms:   检索耗时（毫秒）
        metadata:     扩展元数据
    """
    channel_type: SearchChannelType
    channel_name: str
    chunks: List[RetrievedChunk] = field(default_factory=list)
    latency_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)