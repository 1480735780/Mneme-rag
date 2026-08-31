# -*- coding: utf-8 -*-
"""VectorIntentClassifier 单元测试：懒初始化预计算 / 余弦分类排序 / top_k 过滤 / 空树兜底 / wiring 注入"""
import asyncio
from typing import Dict, List

import pytest

from rag.intent.classifier import VectorIntentClassifier, _cosine_similarity
from rag.intent.model import IntentLevel, IntentNode


# ==================== 伪对象 ====================


class FakeEmbeddingService:
    """按文本返回固定向量；记录调用次数"""

    def __init__(self, vectors: Dict[str, List[float]], default: List[float]):
        self._vectors = vectors
        self._default = default
        self.embed_calls = 0
        self.embed_batch_calls = 0

    async def embed(self, text: str):
        self.embed_calls += 1
        return self._vectors.get(text, self._default)

    async def embed_batch(self, texts: List[str]):
        self.embed_batch_calls += 1
        return [self._vectors.get(t, self._default) for t in texts]


def _leaf(nid: str, path: str, desc: str, examples: List[str], embedding=None) -> IntentNode:
    return IntentNode(
        id=nid, name=path.split(" > ")[-1], description=desc,
        level=IntentLevel.TOPIC, examples=examples, full_path=path, embedding=embedding,
    )


def _tree(leaves: List[IntentNode]) -> List[IntentNode]:
    root = IntentNode(id="root", name="根", full_path="根")
    root.children = leaves
    return [root]


# ==================== 余弦相似度 ====================


def test_cosine_basic_and_edge_cases():
    assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert _cosine_similarity([1, 0], [1, 0, 0]) == 0.0  # 维度不匹配
    assert _cosine_similarity([0, 0], [0, 0]) == 0.0      # 零向量


# ==================== 懒初始化与分类 ====================


def test_classify_sorts_by_cosine_and_caches_leaf_vectors():
    leaf_a = _leaf("a", "科技 > AI", "人工智能相关", ["什么是AI"], embedding=[1, 0, 0])
    leaf_b = _leaf("b", "体育 > 足球", "足球比赛", ["今天有球赛吗"], embedding=[0, 1, 0])
    emb = FakeEmbeddingService({"ai问题": [1, 0, 0]}, [0.0, 0.0, 1.0])

    classifier = VectorIntentClassifier(emb, tree_loader=lambda: _tree([leaf_a, leaf_b]))

    # 首次 classify：预计算节点自带向量（embed_batch 不应被调用，因叶子均已带 embedding）
    scores = asyncio.run(classifier.classify_targets("ai问题"))
    assert emb.embed_batch_calls == 0
    assert [s.node.id for s in scores] == ["a", "b"]
    assert scores[0].score == pytest.approx(1.0)
    assert scores[1].score == pytest.approx(0.0)

    # 懒缓存：第二次不重新 embed 叶子
    asyncio.run(classifier.classify_targets("其他"))
    assert emb.embed_batch_calls == 0


def test_lazy_embed_for_leaves_without_precomputed_vector():
    leaf_a = _leaf("a", "科技 > AI", "人工智能", ["什么是AI"])  # 无预计算向量
    leaf_b = _leaf("b", "体育 > 足球", "足球", ["球赛"], embedding=[0, 1, 0])
    emb = FakeEmbeddingService({"科技 > AI\n人工智能\n什么是AI": [1, 0, 0], "ai问题": [1, 0, 0]}, [0.0, 0.0, 1.0])

    classifier = VectorIntentClassifier(emb, tree_loader=lambda: _tree([leaf_a, leaf_b]))

    scores = asyncio.run(classifier.classify_targets("ai问题"))
    assert emb.embed_batch_calls == 1  # 仅对缺向量的叶子批量 embed
    assert scores[0].node.id == "a"
    assert scores[0].score == pytest.approx(1.0)


def test_top_k_above_threshold_filters_and_truncates():
    leaf_a = _leaf("a", "科技 > AI", "人工智能", ["AI"], embedding=[1, 0, 0])
    leaf_b = _leaf("b", "体育 > 足球", "足球", ["球赛"], embedding=[0.5, 0.5, 0])
    leaf_c = _leaf("c", "文化 > 电影", "电影", ["电影"], embedding=[0.3, 0.9, 0])  # 与 [1,0,0] 余弦≈0.316 < 0.4
    emb = FakeEmbeddingService({"ai问题": [1, 0, 0]}, [0.0, 0.0, 1.0])

    classifier = VectorIntentClassifier(emb, tree_loader=lambda: _tree([leaf_a, leaf_b, leaf_c]))

    result = asyncio.run(classifier.top_k_above_threshold("ai问题", top_n=2, min_score=0.4))
    assert [s.node.id for s in result] == ["a", "b"]  # c 分数低于阈值被过滤


def test_empty_tree_returns_empty():
    emb = FakeEmbeddingService({}, [1, 0, 0])
    classifier = VectorIntentClassifier(emb, tree_loader=lambda: [])

    assert asyncio.run(classifier.classify_targets("问题")) == []
    assert asyncio.run(classifier.top_k_above_threshold("问题", 3, 0.1)) == []


def test_embed_failure_returns_empty_intent():
    class BoomEmbedding(FakeEmbeddingService):
        async def embed(self, text: str):
            raise RuntimeError("embed failed")

    leaf_a = _leaf("a", "科技 > AI", "人工智能", ["AI"], embedding=[1, 0, 0])
    classifier = VectorIntentClassifier(BoomEmbedding({}, [1, 0, 0]), tree_loader=lambda: _tree([leaf_a]))

    assert asyncio.run(classifier.classify_targets("问题")) == []


def test_build_node_text_concatenates_fields():
    node = _leaf("a", "集团信息化 > 人事 > 考勤", "考勤制度", ["迟到怎么办", "打卡规则"])
    text = VectorIntentClassifier.build_node_text(node)
    assert "集团信息化 > 人事 > 考勤" in text
    assert "考勤制度" in text
    assert "迟到怎么办 打卡规则" in text


def test_get_node_by_id_registry():
    leaf_a = _leaf("a", "科技 > AI", "人工智能", ["AI"], embedding=[1, 0, 0])
    classifier = VectorIntentClassifier(FakeEmbeddingService({}, [1, 0, 0]), tree_loader=lambda: _tree([leaf_a]))

    assert classifier.get_node_by_id("a") is leaf_a
    assert classifier.get_node_by_id("nonexistent") is None
    assert classifier.get_node_by_id("") is None


# ==================== wiring 注入 ====================


def test_wiring_vector_classifier_injected(monkeypatch):
    from app.config import AppSettings
    from app.wiring import AppContainer

    monkeypatch.setenv("RAGENT_INTENT_CLASSIFIER", "vector")
    container = AppContainer.build(AppSettings.from_env())
    classifier = container.engine._intent_resolver._classifier
    assert isinstance(classifier, VectorIntentClassifier)


def test_wiring_default_llm_classifier(monkeypatch):
    from app.config import AppSettings
    from app.wiring import AppContainer

    monkeypatch.delenv("RAGENT_INTENT_CLASSIFIER", raising=False)
    from rag.intent.classifier import DefaultIntentClassifier

    container = AppContainer.build(AppSettings.from_env())
    assert isinstance(container.engine._intent_resolver._classifier, DefaultIntentClassifier)
