"""
rag.intent - 意图解析

    - model：意图数据模型（IntentNode + NodeScore + IntentKind + IntentLevel）
    - tree：意图树构建与缓存（IntentTreeFactory + IntentTreeCacheManager + 扁平记录组装）
    - classifier：意图分类器（IntentClassifier + DefaultIntentClassifier + IntentNodeRegistry +
      NodeScoreFilters + 常量）

对应 ragent 源码：
    - rag/core/intent/IntentNode + NodeScore
    - rag/core/intent/IntentTreeFactory + IntentTreeCacheManager
    - rag/core/intent/IntentClassifier + DefaultIntentClassifier + IntentNodeRegistry + NodeScoreFilters
"""
from rag.intent.classifier import (
    INTENT_CLASSIFIER_PROMPT_PATH,
    INTENT_MIN_SCORE,
    MAX_INTENT_COUNT,
    DefaultIntentClassifier,
    IntentCandidate,
    IntentClassifier,
    IntentGroup,
    IntentNodeRegistry,
    IntentResolver,
    IntentTreeData,
    NodeScoreFilters,
    SubQuestionIntent,
)
from rag.intent.model import IntentKind, IntentLevel, IntentNode, NodeScore
from rag.intent.tree import (
    IntentNodeRecord,
    IntentTreeCacheManager,
    IntentTreeFactory,
    build_intent_tree_from_records,
    fill_full_path,
    flatten_intent_tree,
)

__all__ = [
    "INTENT_CLASSIFIER_PROMPT_PATH",
    "INTENT_MIN_SCORE",
    "MAX_INTENT_COUNT",
    "DefaultIntentClassifier",
    "IntentCandidate",
    "IntentClassifier",
    "IntentGroup",
    "IntentKind",
    "IntentLevel",
    "IntentNode",
    "IntentNodeRecord",
    "IntentNodeRegistry",
    "IntentResolver",
    "IntentTreeCacheManager",
    "IntentTreeData",
    "IntentTreeFactory",
    "NodeScore",
    "NodeScoreFilters",
    "SubQuestionIntent",
    "build_intent_tree_from_records",
    "fill_full_path",
    "flatten_intent_tree",
]
