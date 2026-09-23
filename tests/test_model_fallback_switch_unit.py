# -*- coding: utf-8 -*-
"""
P1：指定模型降级开关 allow_fallback 单测（doc: docs/infra/model-fallback-strategy.md 11.1）

语义（对齐 Java 的 `List.of(resolveTarget(modelId))` vs `selectEmbeddingCandidates()` 两条路径）：
    - 默认 allow_fallback=False：指定 model_id 失败即抛、不降级（现状行为锁定）；
    - allow_fallback=True：指定模型置首 + 默认候选追加（去重），经 RoutingExecutor 故障转移；
    - allow_fallback=True 但 model_id 不在候选 → RoutingExecutionError fail-fast；
    - allow_fallback=True 但指定模型选择期不可用（熔断/禁用，selector 过滤后无该 id）
      → 同样 fail-fast，不降级、其余候选不被尝试（只管"调用失败"的语义固化）；
    - model_id=None 时 allow_fallback 无意义，仍走默认多候选路由；
    - chat：preferred_model_id 默认可降级（现状锁定，含未登记 preferred 被 selector
      静默忽略回退档位候选）；allow_fallback=False → 只用指定模型，失败即抛且
      不尝试档位其余候选。

executor 桩内部委托真实 RoutingExecutor：既锁定传入的候选列表形态，也锁定真实故障转移行为。
"""
import asyncio
from typing import List, Optional

import pytest

from core.llm.chat import RoutingLLMService
from core.llm.config.config import ModelCandidate, ProviderConfig
from core.llm.embedding import RoutingEmbeddingService
from core.llm.model.health_store import ModelHealthStore
from core.llm.model.model_target import ModelTarget
from core.llm.model.routing_executor import RoutingExecutionError, RoutingExecutor
from core.llm.providers.base import BaseChatClient
from core.llm.providers.base_embedding import BaseEmbeddingClient
from core.llm.providers.base_rerank import BaseRerankClient
from core.llm.reranker import RoutingRerankService
from core.llm.schema import ChatRequest, Message, RetrievedChunk


# ==================== 桩件 ====================


def _target(model_id: str, provider: str = "p1", dimension: Optional[int] = None) -> ModelTarget:
    candidate = ModelCandidate(
        id=model_id, provider=provider, model=model_id, dimension=dimension
    )
    return ModelTarget(
        id=candidate.id,
        candidate=candidate,
        provider=ProviderConfig(url="https://example.com", api_key="test-key"),
    )


class _RecordingExecutor:
    """记录 executor 收到的候选列表，内部委托真实 RoutingExecutor（锁定真实故障转移语义）。"""

    def __init__(self, health_store: ModelHealthStore):
        self._inner = RoutingExecutor(health_store)
        self.calls: List[List[str]] = []

    async def execute_with_fallback(self, capability, targets, client_resolver, caller):
        self.calls.append([t.id for t in targets])
        return await self._inner.execute_with_fallback(
            capability, targets, client_resolver, caller
        )


class _EmbeddingStubClient(BaseEmbeddingClient):
    """记录 embed / embed_batch 调用的桩客户端；fail_ids 中的模型调用即失败。"""

    provider = "p1"

    def __init__(self, fail_ids=()):
        self.calls: List[str] = []
        self.fail_ids = set(fail_ids)

    async def embed(self, text, target):
        self.calls.append(target.id)
        if target.id in self.fail_ids:
            raise RuntimeError(f"embed boom: {target.id}")
        return [1.0]

    async def embed_batch(self, texts, target):
        self.calls.append(target.id)
        if target.id in self.fail_ids:
            raise RuntimeError(f"embed boom: {target.id}")
        return [[1.0] for _ in texts]


class _RerankStubClient(BaseRerankClient):
    provider = "p1"

    def __init__(self, fail_ids=()):
        self.calls: List[str] = []
        self.fail_ids = set(fail_ids)

    async def rerank(self, query, candidates, top_n, target):
        self.calls.append(target.id)
        if target.id in self.fail_ids:
            raise RuntimeError(f"rerank boom: {target.id}")
        return list(candidates)


class _ChatStubClient(BaseChatClient):
    provider = "c1"

    def __init__(self, fail_ids=()):
        self.calls: List[str] = []
        self.fail_ids = set(fail_ids)

    async def chat(self, request, target):
        self.calls.append(target.id)
        if target.id in self.fail_ids:
            raise RuntimeError(f"chat boom: {target.id}")
        return f"ok:{target.id}"

    async def stream_chat(self, request, callback, target):
        raise AssertionError("流式不在本测试范围")


class _ServiceSelector:
    """embedding / rerank 选择器桩：固定返回候选列表。"""

    def __init__(self, targets: List[ModelTarget]):
        self._targets = list(targets)

    def select_embedding_candidates(self) -> List[ModelTarget]:
        return list(self._targets)

    def select_rerank_candidates(self) -> List[ModelTarget]:
        return list(self._targets)


class _ChatSelectorStub:
    """chat 选择器桩：模拟真实 selector 的 preferred 语义（置首 + 档位候选追加 + 去重，
    未登记的 preferred 忽略并回退档位候选）。"""

    def __init__(self, targets: List[ModelTarget]):
        self._targets = list(targets)

    def select_chat_candidates(self, thinking, override=None, preferred_model_id=None):
        ids = [t.id for t in self._targets]
        ordered: List[str] = []
        if preferred_model_id and preferred_model_id.strip() and preferred_model_id in ids:
            ordered.append(preferred_model_id)
        ordered.extend(i for i in ids if i not in ordered)
        by_id = {t.id: t for t in self._targets}
        return [by_id[i] for i in ordered]


def _run(coro):
    return asyncio.run(coro)


_CHUNKS = [RetrievedChunk(id="c1", text="t1", score=0.9)]


# ==================== embedding ====================


def _embed_service(targets, client, executor):
    return RoutingEmbeddingService(
        selector=_ServiceSelector(targets), executor=executor, clients=[client]
    )


class TestEmbeddingAllowFallback:
    def test_default_no_fallback_fails_fast(self):
        """默认 allow_fallback=False：指定 model_id 失败即抛、不降级（现状锁定）。"""
        client = _EmbeddingStubClient(fail_ids={"e1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _embed_service([_target("e1"), _target("e2")], client, executor)

        async def scenario():
            return await service.embed("hi", model_id="e1")

        with pytest.raises(RuntimeError, match="embed boom: e1"):
            _run(scenario())
        assert client.calls == ["e1"]  # 只试了指定模型
        assert executor.calls == []  # 直连路径不经 executor

    def test_allow_fallback_switches_to_next_candidate(self):
        """allow_fallback=True：指定模型置首 + 默认候选追加，失败后切换下一候选。"""
        client = _EmbeddingStubClient(fail_ids={"e1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _embed_service([_target("e1"), _target("e2")], client, executor)

        async def scenario():
            return await service.embed("hi", model_id="e1", allow_fallback=True)

        assert _run(scenario()) == [1.0]  # e2 成功
        assert executor.calls == [["e1", "e2"]]  # 指定置首 + 追加去重
        assert client.calls == ["e1", "e2"]

    def test_allow_fallback_rejects_unknown_model(self):
        """allow_fallback=True 但 model_id 不在候选 → RoutingExecutionError fail-fast。"""
        client = _EmbeddingStubClient()
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _embed_service([_target("e1"), _target("e2")], client, executor)

        async def scenario():
            return await service.embed("hi", model_id="ghost", allow_fallback=True)

        with pytest.raises(RoutingExecutionError, match="ghost"):
            _run(scenario())
        assert executor.calls == []  # 未进入故障转移
        assert client.calls == []

    def test_embed_batch_allow_fallback_switches(self):
        """embed_batch 与 embed 同构：降级时仍走批量接口（不退回逐条）。"""
        client = _EmbeddingStubClient(fail_ids={"e1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _embed_service([_target("e1"), _target("e2")], client, executor)

        async def scenario():
            return await service.embed_batch(["a", "b"], model_id="e1", allow_fallback=True)

        assert _run(scenario()) == [[1.0], [1.0]]
        assert executor.calls == [["e1", "e2"]]
        assert client.calls == ["e1", "e2"]

    def test_allow_fallback_ignored_without_model_id(self):
        """model_id=None 时 allow_fallback 无意义，仍走默认多候选路由。"""
        client = _EmbeddingStubClient()
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _embed_service([_target("e1"), _target("e2")], client, executor)

        async def scenario():
            return await service.embed("hi", allow_fallback=True)

        assert _run(scenario()) == [1.0]
        assert executor.calls == [["e1", "e2"]]
        assert client.calls == ["e1"]

    def test_allow_fallback_dedups_preferred(self):
        """指定模型已在默认候选内时置首去重，不重复尝试。"""
        client = _EmbeddingStubClient()
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _embed_service([_target("e1"), _target("e2")], client, executor)

        async def scenario():
            return await service.embed("hi", model_id="e2", allow_fallback=True)

        assert _run(scenario()) == [1.0]
        assert executor.calls == [["e2", "e1"]]

    def test_allow_fallback_selection_stage_unavailable_fails_fast(self):
        """选择期不可用（模拟熔断/禁用：selector 过滤后无该 id）+ allow_fallback=True
        → RoutingExecutionError，不降级、其余候选不被尝试。

        锁定语义：allow_fallback 只管"调用失败"，选择期不可用一律 fail-fast。
        """
        # e1 被 selector 过滤（如熔断中），列表里只剩 e2
        client = _EmbeddingStubClient()
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _embed_service([_target("e2")], client, executor)

        async def scenario():
            return await service.embed("hi", model_id="e1", allow_fallback=True)

        with pytest.raises(RoutingExecutionError, match="e1"):
            _run(scenario())
        assert executor.calls == []  # fail-fast 发生在进入 executor 之前
        assert client.calls == []  # e2 也未被尝试


# ==================== rerank ====================


def _rerank_service(targets, client, executor):
    return RoutingRerankService(
        selector=_ServiceSelector(targets), executor=executor, clients=[client]
    )


class TestRerankAllowFallback:
    def test_default_no_fallback_fails_fast(self):
        """默认 allow_fallback=False：指定 model_id 失败即抛、不降级（现状锁定）。"""
        client = _RerankStubClient(fail_ids={"r1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _rerank_service([_target("r1"), _target("r2")], client, executor)

        async def scenario():
            return await service.rerank("q", _CHUNKS, 2, model_id="r1")

        with pytest.raises(RuntimeError, match="rerank boom: r1"):
            _run(scenario())
        assert client.calls == ["r1"]
        assert executor.calls == []

    def test_allow_fallback_switches_to_next_candidate(self):
        client = _RerankStubClient(fail_ids={"r1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _rerank_service([_target("r1"), _target("r2")], client, executor)

        async def scenario():
            return await service.rerank("q", _CHUNKS, 2, model_id="r1", allow_fallback=True)

        assert _run(scenario()) == _CHUNKS
        assert executor.calls == [["r1", "r2"]]
        assert client.calls == ["r1", "r2"]

    def test_allow_fallback_rejects_unknown_model(self):
        client = _RerankStubClient()
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _rerank_service([_target("r1"), _target("r2")], client, executor)

        async def scenario():
            return await service.rerank("q", _CHUNKS, 2, model_id="ghost", allow_fallback=True)

        with pytest.raises(RoutingExecutionError, match="ghost"):
            _run(scenario())
        assert executor.calls == []
        assert client.calls == []


# ==================== chat ====================


def _chat_service(targets, client, executor):
    return RoutingLLMService(
        selector=_ChatSelectorStub(targets),
        health_store=ModelHealthStore(2, 30000),
        executor=executor,
        clients=[client],
    )


_REQUEST = ChatRequest(messages=[Message.user("hi")])


class TestChatAllowFallback:
    def test_preferred_fallback_default_succeeds(self):
        """现状锁定：preferred 默认降级——失败后回退档位其余候选。"""
        client = _ChatStubClient(fail_ids={"m1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _chat_service([_target("m1", "c1"), _target("m2", "c1")], client, executor)

        async def scenario():
            return await service.chat(_REQUEST, preferred_model_id="m1")

        assert _run(scenario()) == "ok:m2"
        assert executor.calls == [["m1", "m2"]]
        assert client.calls == ["m1", "m2"]

    def test_preferred_no_fallback_uses_specified_only(self):
        """allow_fallback=False：只用指定模型，失败即抛且不尝试其余候选。"""
        client = _ChatStubClient(fail_ids={"m1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _chat_service([_target("m1", "c1"), _target("m2", "c1")], client, executor)

        async def scenario():
            return await service.chat(
                _REQUEST, preferred_model_id="m1", allow_fallback=False
            )

        with pytest.raises(RoutingExecutionError, match="m1"):
            _run(scenario())
        assert executor.calls == [["m1"]]  # 只有指定模型一个候选
        assert client.calls == ["m1"]  # m2 从未被尝试

    def test_preferred_no_fallback_success_returns_specified(self):
        """allow_fallback=False 且指定模型成功：正常返回，行为与降级路径的成功分支一致。"""
        client = _ChatStubClient()
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _chat_service([_target("m1", "c1"), _target("m2", "c1")], client, executor)

        async def scenario():
            return await service.chat(
                _REQUEST, preferred_model_id="m1", allow_fallback=False
            )

        assert _run(scenario()) == "ok:m1"
        assert executor.calls == [["m1"]]
        assert client.calls == ["m1"]

    def test_preferred_no_fallback_unknown_rejected(self):
        """allow_fallback=False 且 preferred 未登记 → fail-fast（与 embedding/rerank 对齐）。"""
        client = _ChatStubClient()
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _chat_service([_target("m1", "c1"), _target("m2", "c1")], client, executor)

        async def scenario():
            return await service.chat(
                _REQUEST, preferred_model_id="ghost", allow_fallback=False
            )

        with pytest.raises(RoutingExecutionError, match="ghost"):
            _run(scenario())
        assert executor.calls == []
        assert client.calls == []

    def test_preferred_unregistered_default_silently_falls_back(self):
        """现状锁定：默认 allow_fallback=True 下，未登记 preferred 由 selector
        静默忽略并回退档位候选（与 embedding/rerank 的 fail-fast 相反，这是
        chat 既有语义——默认路径不经 _only_preferred 校验）。"""
        client = _ChatStubClient(fail_ids={"m1"})
        executor = _RecordingExecutor(ModelHealthStore(2, 30000))
        service = _chat_service([_target("m1", "c1"), _target("m2", "c1")], client, executor)

        async def scenario():
            return await service.chat(_REQUEST, preferred_model_id="ghost")

        assert _run(scenario()) == "ok:m2"  # 不抛错，档位候选接管
        assert executor.calls == [["m1", "m2"]]
        assert client.calls == ["m1", "m2"]
