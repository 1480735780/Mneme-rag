# -*- coding: utf-8 -*-
"""
精排链路装配测试：RerankProperties / AppContainer._build_rerank_service / _build_retrieval_engine 接线
（对应 Java RAGConfigProperties.rerankEnabled + RoutingRerankService @Bean + RerankPostProcessor 链上装配）

覆盖：
    - RerankProperties：env 解析、默认关闭（偏离 Java true 的理由见 config docstring）
    - _build_rerank_service：ai.yaml 形态配置按 provider 建 client，缺 key → None
    - 处理链组装：默认无 Rerank；开关 + 服务注入 → Rerank(order=10) 入链、闸门 rerank_enabled=True；
      开关开而无客户端 → Rerank 不入链、闸门关闭
    - fail-fast 集成：闸门 min-rerank-score>0 而精排未挂 → _build_retrieval_engine 直接抛
"""
import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from rag.retrieval.config import EvidenceProperties, RerankProperties
from rag.retrieval.postprocessor.dedup import DeduplicationPostProcessor
from rag.retrieval.postprocessor.evidence_gate import EvidenceGatePostProcessor
from rag.retrieval.postprocessor.fusion import FusionPostProcessor
from rag.retrieval.postprocessor.metadata_enrichment import MetadataEnrichmentPostProcessor
from rag.retrieval.postprocessor.rerank import RerankPostProcessor
from storage.cache import MemoryCacheManager
from storage.database import InMemoryDatabaseClient


def _container() -> AppContainer:
    return AppContainer(
        settings=AppSettings(stack_profile="memory"),
        db=InMemoryDatabaseClient(),
        cache=MemoryCacheManager(),
    )


def _chain(container: AppContainer):
    """构建检索引擎并返回 (后处理器列表, 证据闸门)"""
    engine = container._build_retrieval_engine()
    gate = next(p for p in engine._postprocessors if isinstance(p, EvidenceGatePostProcessor))
    return engine._postprocessors, gate


# ==================== RerankProperties ====================


class TestRerankProperties:
    def test_default_off(self):
        # 偏离 Java true：默认部署无 rerank key，开着只会空转异常（见 RerankProperties docstring）
        assert RerankProperties().enabled is False

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("RAGENT_RERANK_ENABLED", "on")
        assert RerankProperties.from_env().enabled is True
        monkeypatch.setenv("RAGENT_RERANK_ENABLED", "0")
        assert RerankProperties.from_env().enabled is False


# ==================== _build_rerank_service ====================


class _Provider:
    def __init__(self, api_key: str):
        self.api_key = api_key


class _FakeAiConfig:
    def __init__(self, providers, selection=None):
        self.providers = providers
        self.selection = selection


class TestBuildRerankService:
    def test_builds_with_resolved_key(self):
        container = _container()
        config = _FakeAiConfig({"siliconflow": _Provider("sk-test")})
        service = container._build_rerank_service(config)
        assert service is not None  # RoutingRerankService

    def test_skips_unresolved_placeholder_key(self):
        # ai.yaml 的 ${SILICONFLOW_API_KEY} 未解析 → 无 key，不进入候选 → None
        container = _container()
        config = _FakeAiConfig({"siliconflow": _Provider("${SILICONFLOW_API_KEY}")})
        assert container._build_rerank_service(config) is None

    def test_skips_unknown_providers(self):
        container = _container()
        config = _FakeAiConfig({"qwen": _Provider("sk-test"), "openai": _Provider("sk-test")})
        assert container._build_rerank_service(config) is None  # 无 rerank 客户端实现

    def test_injection_slot_priority(self):
        # _get_shared_rerank：注入槽优先且不落缓存（测试桩可后注入）
        container = _container()
        stub = object()
        container.rerank_service = stub
        assert container._get_shared_rerank() is stub


# ==================== 处理链组装 ====================


class TestChainAssembly:
    def test_default_chain_without_rerank(self):
        # 默认（RAGENT_RERANK_ENABLED 未开）：Dedup → Fusion → EvidenceGate → MetadataEnrichment
        container = _container()
        chain, gate = _chain(container)
        assert [type(p) for p in chain] == [
            DeduplicationPostProcessor,
            FusionPostProcessor,
            EvidenceGatePostProcessor,
            MetadataEnrichmentPostProcessor,
        ]
        assert gate._rerank_enabled is False

    def test_rerank_in_chain_when_enabled(self):
        container = _container()
        container.rerank_service = object()  # 测试桩：有可用精排服务
        container.rerank_properties = RerankProperties(enabled=True)
        chain, gate = _chain(container)
        orders = [p.get_order() for p in chain]
        assert orders == sorted(orders)  # 链按 order 升序
        rerank = next(p for p in chain if isinstance(p, RerankPostProcessor))
        assert rerank.get_order() == 10
        assert gate._rerank_enabled is True
        # 完整 v2 形态：Dedup(1) → Fusion(5) → Rerank(10) → EvidenceGate(15) → MetadataEnrichment(20)
        assert orders == [1, 5, 10, 15, 20]

    def test_rerank_skipped_when_enabled_but_no_client(self, caplog):
        # 开关开了但没有可用客户端（如未配 key）：Rerank 不入链，闸门关闭 + warn
        container = _container()
        container.rerank_properties = RerankProperties(enabled=True)
        with caplog.at_level("WARNING"):
            chain, gate = _chain(container)
        assert not any(isinstance(p, RerankPostProcessor) for p in chain)
        assert gate._rerank_enabled is False
        assert any("精排链路不装配" in r.message for r in caplog.records)

    def test_gate_min_score_without_rerank_fails_fast(self, monkeypatch):
        # 集成断言：闸门开（min>0）而精排未挂 → 构建检索引擎处直接抛（对齐 Java afterPropertiesSet 时机）
        monkeypatch.setenv("RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE", "0.2")
        container = _container()
        with pytest.raises(ValueError, match="需要精排出分"):
            container._build_retrieval_engine()

    def test_gate_on_with_rerank_ready_starts(self, monkeypatch):
        # 闸门开 + 精排就绪：引擎正常装配，闸门激活
        monkeypatch.setenv("RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE", "0.2")
        container = _container()
        container.rerank_service = object()
        container.rerank_properties = RerankProperties(enabled=True)
        chain, gate = _chain(container)
        assert gate.is_enabled(None) is True
        assert gate._properties.min_rerank_score == pytest.approx(0.2)
        assert any(isinstance(p, RerankPostProcessor) for p in chain)


# ==================== EvidenceProperties 默认回归 ====================


def test_evidence_default_off_regression(monkeypatch):
    """闸门默认关闭（min=0）：与 RerankProperties 默认关闭配套，默认部署行为不变"""
    monkeypatch.delenv("RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE", raising=False)
    assert EvidenceProperties.from_env().min_rerank_score == 0.0
