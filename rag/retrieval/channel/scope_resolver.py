"""
检索作用域解析器（对应 ragent RetrievalScopeResolver）

由引擎按子问题各算一次放进 SearchContext，各通道只读不判，
避免同一子问题里向量走全局、关键词走定向这类作用域打架。

判定只看 KB 意图最高分：达到置信阈值才收窄，意图个数不参与判定——
多一个低分意图不应让系统更准确。收窄（定向）时命中库进主检索范围、
未命中库进补充范围（兜住意图判错导致的漏召回）；置信不足 / 无 KB 意图 /
绑定库全部失效时退化为全局作用域。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.retrieval.channel.RetrievalScopeResolver
"""
from __future__ import annotations

import logging
from typing import List

from rag.intent import NodeScore, NodeScoreFilters
from rag.intent.model import IntentNode
from rag.retrieval.channel.kb_collection_provider import KbCollectionProvider
from rag.retrieval.config import ScopeProperties
from rag.retrieval.schema import RetrievalScope

logger = logging.getLogger(__name__)


class RetrievalScopeResolver:
    """
    检索作用域解析器（对应 Java RetrievalScopeResolver）

    Args:
        properties:             检索作用域配置（min_intent_score / confidence_threshold / ...）
        kb_collection_provider: 有效知识库 collection 提供者（全库范围唯一来源）
    """

    def __init__(
        self,
        properties: ScopeProperties,
        kb_collection_provider: KbCollectionProvider,
    ):
        self._properties = properties
        self._kb_collection_provider = kb_collection_provider

    def resolve(self, sub_intents: List) -> RetrievalScope:
        """
        解析本次请求的检索作用域（对应 Java resolve）

        Args:
            sub_intents: 子问题意图列表（SubQuestionIntent）

        Returns:
            RetrievalScope: 定向作用域（置信足够且命中库仍有有效库），否则全局作用域
        """
        active_collections = self._kb_collection_provider.list_active_collections()
        kb_intents = self._extract_kb_intents(sub_intents)
        top_score = max((ns.score for ns in kb_intents), default=0.0)

        if not kb_intents:
            logger.info("未识别出有效 KB 意图，检索走全局作用域")
            return RetrievalScope.global_scope(top_score, active_collections)

        threshold = self._properties.confidence_threshold
        if top_score < threshold:
            logger.info("KB 意图置信度过低（%s < %s），检索走全局作用域", top_score, threshold)
            return RetrievalScope.global_scope(top_score, active_collections)

        targets = NodeScoreFilters.kb_collections(kb_intents)
        # 意图绑定与知识库表是两套数据，删库不回写意图节点。绑定的库全部失效时若照常收窄，
        # 主路名额会全部打在空库上、补充路只剩 25%，而每条查询都「成功」返回 0 条，没有任何报错
        if not any(collection in targets for collection in active_collections):
            logger.warning("KB 意图绑定的知识库均已失效（%s），检索退化为全局作用域", targets)
            return RetrievalScope.global_scope(top_score, active_collections)

        supplement = [c for c in active_collections if c not in targets]
        logger.info(
            "KB 意图置信度充足（%s），检索收窄到 %d 个命中库，补充范围 %d 个库",
            top_score, len(targets), len(supplement),
        )
        return RetrievalScope(
            directed=True,
            top_score=top_score,
            intents=kb_intents,
            target_collections=targets,
            supplement_collections=supplement,
        )

    def _extract_kb_intents(self, sub_intents: List) -> List[NodeScore]:
        """
        提取达到最低分、真正绑定了 collection 且按意图节点去重的 KB 意图
        （对应 Java extractKbIntents）

        去重是必须的：意图按子问题分别识别，多个子问题命中同一节点会产出多条 NodeScore，
        而定向路按 NodeScore 逐条 fan-out —— 不去重则同一库被查多次，同一 chunk 在通道原始
        列表里占多个名次，下游 RRF 按名次累加会把它的分数翻倍；同时也会虚高「通道产能」，
        连带把补充路名额算大。

        绑定为空的意图既检索不到东西，也不该拉高置信度判定。

        Returns:
            List[NodeScore]: 按首次出现顺序、同一节点保留最高分的 KB 意图列表
        """
        if not sub_intents:
            return []
        min_score = self._properties.min_intent_score
        all_scores: List[NodeScore] = [
            ns for si in sub_intents for ns in si.node_scores
        ]
        kb_intents = NodeScoreFilters.kb_with_min_score(all_scores, min_score)

        # 按节点 ID 去重、保留最高分；dict 保序使结果与 Java LinkedHashMap 一致
        best_by_node_id: dict = {}
        for ns in kb_intents:
            node: IntentNode = ns.node
            if not node.get_effective_collection_names():
                continue
            current = best_by_node_id.get(node.id)
            if current is None or ns.score > current.score:
                best_by_node_id[node.id] = ns
        return list(best_by_node_id.values())
