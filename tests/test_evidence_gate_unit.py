# -*- coding: utf-8 -*-
"""
证据相关性闸门单元测试：EvidenceGatePostProcessor + EvidenceProperties + 精排分双写
（对应 Java EvidenceGatePostProcessor / SearchChannelProperties.Evidence / BaiLianRerankClient）

覆盖：
    - 闸门行为：整批最高精排分判定、全批缺分空转放行、非有限值（NaN/±Inf）视同缺分、
      0 = 关闭（is_enabled 恒 False）、空批次直通
    - 启动校验：min_rerank_score > 0 且精排未启用 → fail-fast（对齐 Java afterPropertiesSet）
    - 配置：from_env 解析 / 非法值 / NaN 与 >1 范围校验（对齐 Java setter 校验）
    - 精排客户端双写：relevance_score 同时落 score 与 rerank_score；补位/未出分候选压 0 沉底（unscored）
"""
import pytest

from core.llm.providers.bailian_rerank import BaiLianRerankClient
from core.llm.schema import RetrievedChunk
from rag.retrieval.config import EvidenceProperties
from rag.retrieval.postprocessor.evidence_gate import EvidenceGatePostProcessor
from rag.retrieval.schema import SearchContext


def _chunk(chunk_id: str, rerank_score=None, score=None) -> RetrievedChunk:
    return RetrievedChunk(id=chunk_id, text=f"内容 {chunk_id}", score=score, rerank_score=rerank_score)


def _gate(min_rerank_score: float, rerank_enabled: bool = True) -> EvidenceGatePostProcessor:
    return EvidenceGatePostProcessor(
        EvidenceProperties(min_rerank_score=min_rerank_score), rerank_enabled=rerank_enabled
    )


def _process_sync(gate: EvidenceGatePostProcessor, chunks):
    """process 是 async，测试直跑（链路真实形态为 await）"""
    import asyncio

    return asyncio.run(gate.process(chunks, results=[], context=SearchContext(original_question="q")))


# ==================== 闸门行为 ====================


class TestEvidenceGateBehavior:
    def test_name_and_order(self):
        gate = _gate(0.2)
        assert gate.get_name() == "EvidenceGate"
        assert gate.get_order() == 15  # Rerank(10) 之后、MetadataEnrichment(20) 之前

    def test_enabled_by_threshold(self):
        assert _gate(0.2).is_enabled(SearchContext(original_question="q")) is True
        assert _gate(0.0).is_enabled(SearchContext(original_question="q")) is False  # 0 = 关闭

    def test_empty_chunks_passthrough(self):
        assert _process_sync(_gate(0.2), []) == []

    def test_batch_above_threshold_keeps_all(self):
        # 整批最高分过线：全批保留（含弱证据——只管批级去留）
        chunks = [_chunk("a", rerank_score=0.9), _chunk("b", rerank_score=0.01), _chunk("c")]
        assert _process_sync(_gate(0.2), chunks) == chunks

    def test_batch_below_threshold_drops_all(self):
        chunks = [_chunk("a", rerank_score=0.1), _chunk("b", rerank_score=0.19)]
        assert _process_sync(_gate(0.2), chunks) == []

    def test_max_over_finite_scores_only(self):
        # None / NaN / ±Inf 都视同缺分，不参与最高分；仅剩的有限分照常判定
        chunks = [
            _chunk("a", rerank_score=None),
            _chunk("b", rerank_score=float("nan")),
            _chunk("c", rerank_score=float("inf")),
            _chunk("d", rerank_score=float("-inf")),
            _chunk("e", rerank_score=0.05),
        ]
        assert _process_sync(_gate(0.2), chunks) == []  # max=0.05 < 0.2 → 整批丢弃

    def test_no_scores_at_all_passes_through(self, caplog):
        # 无分可读：noop 降级 / 精排异常时的兜底——放行并 warn（空转标记）
        chunks = [_chunk("a", score=0.9), _chunk("b")]
        with caplog.at_level("WARNING"):
            result = _process_sync(_gate(0.2), chunks)
        assert result == chunks
        assert any("无精排分可读" in r.message for r in caplog.records)

    def test_boundary_score_passes(self):
        # 恰好等于下限：>= 语义放行
        chunks = [_chunk("a", rerank_score=0.2)]
        assert _process_sync(_gate(0.2), chunks) == chunks


# ==================== 启动校验 ====================


class TestEvidenceGateValidation:
    def test_gate_on_rerank_off_raises(self):
        # 对齐 Java afterPropertiesSet：闸门开而精排关 = 恒放行空转，fail-fast
        with pytest.raises(ValueError, match="min-rerank-score.*需要精排出分"):
            _gate(0.2, rerank_enabled=False)

    def test_gate_on_rerank_on_ok(self):
        gate = _gate(0.2, rerank_enabled=True)
        assert gate.get_order() == 15

    def test_gate_off_rerank_off_ok(self):
        # 默认形态：闸门关闭，精排未接线不阻塞启动
        _gate(0.0, rerank_enabled=False)


# ==================== 配置 ====================


class TestEvidenceProperties:
    def test_default_is_off(self):
        # 偏离 Java 默认 0.2：Python 精排链未接线，默认关闭（见 EvidenceProperties docstring）
        assert EvidenceProperties().min_rerank_score == 0.0

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE", "0.35")
        assert EvidenceProperties.from_env().min_rerank_score == pytest.approx(0.35)

    def test_from_env_unset_defaults(self, monkeypatch):
        monkeypatch.delenv("RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE", raising=False)
        assert EvidenceProperties.from_env().min_rerank_score == 0.0

    def test_from_env_invalid_string(self, monkeypatch):
        monkeypatch.setenv("RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE", "abc")
        with pytest.raises(ValueError, match="RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE"):
            EvidenceProperties.from_env()

    def test_rejects_nan(self):
        with pytest.raises(ValueError, match="非法"):
            EvidenceProperties(min_rerank_score=float("nan"))

    def test_rejects_above_one(self):
        with pytest.raises(ValueError, match="非法"):
            EvidenceProperties(min_rerank_score=1.5)

    def test_accepts_range(self):
        assert EvidenceProperties(min_rerank_score=0.0).min_rerank_score == 0.0
        assert EvidenceProperties(min_rerank_score=1.0).min_rerank_score == 1.0


# ==================== 精排分双写 ====================


class TestBaiLianRerankDoubleWrite:
    def _client(self) -> BaiLianRerankClient:
        return BaiLianRerankClient(http_client=None)

    def _extract(self, candidates, results, top_n):
        return self._client()._extract_results(
            {"output": {"results": results}}, candidates, top_n
        )

    def test_scored_candidate_writes_both_fields(self):
        candidates = [_chunk("a", score=0.03), _chunk("b", score=0.02)]
        out = self._extract(candidates, [{"index": 1, "relevance_score": 0.87}], 2)
        assert out[0].id == "b"
        assert out[0].score == pytest.approx(0.87)
        # 同一个分写两处：rerank_score 留给证据闸门
        assert out[0].rerank_score == pytest.approx(0.87)

    def test_padded_candidate_sunk_to_zero(self):
        # 精排未命中的补位候选：score 压 0 沉底、不带 rerank_score（对齐 Java unscored——
        # 留着 RRF 分会混两把尺子；闸门据此认出「没经过精排」）
        candidates = [_chunk("a", score=0.03), _chunk("b", score=0.02)]
        out = self._extract(candidates, [{"index": 0, "relevance_score": 0.9}], 2)
        assert out[0].rerank_score == pytest.approx(0.9)
        assert out[1].id == "b"
        assert out[1].score == 0.0
        assert out[1].rerank_score is None

    def test_non_finite_relevance_sunk_to_zero(self):
        candidates = [_chunk("a", score=0.03)]
        out = self._extract(candidates, [{"index": 0, "relevance_score": float("nan")}], 1)
        # 非有限值不写分（对齐 Java 逐项校验）→ unscored：score 压 0、无 rerank_score
        assert out[0].score == 0.0
        assert out[0].rerank_score is None
