# -*- coding: utf-8 -*-
"""
P2：降级安全防护与可观测性单测（doc: docs/infra/model-fallback-strategy.md 11.2 / 11.3）

11.2 维度一致性校验（RoutingEmbeddingService）：
    - 指定模型声明 dimension 时，allow_fallback=True 的追加候选仅保留同维度者；
      维度不匹配 / 未声明的候选剔除并告警（防止降级到不同维度模型导致向量库
      索引与查询维度不匹配）；
    - 指定模型未声明 dimension（None）→ 无法校验，不过滤（可用性优先）；
    - 默认路由（model_id=None）不在守卫范围：维度一致性由配置负责（守卫只管
      "降级"场景，与 11.2 的范围声明一致）。

11.3 结构化降级日志：
    - RoutingExecutor 失败日志携带 capability / failed_model_id / failed_provider /
      error_type / error_message / fallback_count 结构化字段（extra）；
    - chat 流式两条降级日志（首包前失败 / 首包后失败）字段对齐 executor。
"""
import asyncio

import pytest

from core.llm.callback import BaseStreamCallback
from core.llm.chat import RoutingLLMService
from core.llm.config.config import ModelCandidate, ProviderConfig
from core.llm.embedding import RoutingEmbeddingService
from core.llm.model.health_store import HealthState, ModelHealth, ModelHealthStore
from core.llm.model.model_target import ModelTarget
from core.llm.model.routing_executor import RoutingExecutionError, RoutingExecutor
from core.llm.providers.base import BaseChatClient
from core.llm.providers.base_embedding import BaseEmbeddingClient
from core.llm.schema import ChatRequest, Message


# ==================== 桩件 ====================


def _dim_target(model_id: str, dimension, provider: str = "p1") -> ModelTarget:
    candidate = ModelCandidate(
        id=model_id, provider=provider, model=model_id, dimension=dimension
    )
    return ModelTarget(
        id=candidate.id,
        candidate=candidate,
        provider=ProviderConfig(url="https://example.com", api_key="test-key"),
    )


class _EmbeddingStubClient(BaseEmbeddingClient):
    """fail_ids 中的模型调用即失败，其余成功；记录调用序。"""

    provider = "p1"

    def __init__(self, fail_ids=()):
        self.calls = []
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


class _SelectorStub:
    def __init__(self, targets):
        self._targets = list(targets)

    def select_embedding_candidates(self):
        return list(self._targets)


class _ChatSelectorStub:
    def __init__(self, targets):
        self._targets = list(targets)

    def select_chat_candidates(self, thinking, override=None, preferred_model_id=None):
        return list(self._targets)


class _StreamStubClient(BaseChatClient):
    """mode: error=首包前失败 / midstream=首包后失败 / ok=正常完成。"""

    def __init__(self, provider: str, mode: str):
        self._provider = provider
        self._mode = mode

    @property
    def provider(self) -> str:
        return self._provider

    async def chat(self, request, target):
        raise AssertionError("非流式不在本测试范围")

    async def stream_chat(self, request, callback, target):
        if self._mode == "error":
            await callback.on_error(RuntimeError("stream boom"))
            return
        if self._mode == "midstream":
            await callback.on_content("partial")
            # 让出控制权：确保主协程先拿到 SUCCESS 判定（真实时序：
            # 首包到达 → 判定成功 → 流继续 → 中途出错），否则整个任务
            # 会在主协程恢复前跑完，探测直接拿到 ERROR（退化为路径 B）
            await asyncio.sleep(0)
            await callback.on_error(RuntimeError("mid boom"))
            return
        await callback.on_start()
        await callback.on_content("hi")
        await callback.on_complete()


def _run(coro):
    return asyncio.run(coro)


# ==================== 11.2 维度一致性校验 ====================


def _embed_service(targets, client):
    return RoutingEmbeddingService(
        selector=_SelectorStub(targets),
        executor=RoutingExecutor(ModelHealthStore(2, 30000)),
        clients=[client],
    )


class TestDimensionGuard:
    def test_fallback_filters_dimension_mismatch(self):
        """指定模型声明 dim=768：维度不匹配的候选被剔除，同维度候选接管降级。"""
        client = _EmbeddingStubClient(fail_ids={"e1"})
        service = _embed_service(
            [_dim_target("e1", 768), _dim_target("e2", 1024), _dim_target("e3", 768)],
            client,
        )

        async def scenario():
            return await service.embed("hi", model_id="e1", allow_fallback=True)

        assert _run(scenario()) == [1.0]  # e3（同维度）接管，而非 e2（1024）
        assert client.calls == ["e1", "e3"]

    def test_fallback_skips_undeclared_dimension_candidates(self, caplog):
        """指定模型声明 dim=768：未声明维度的候选同样被剔除（向量完整性优先），
        全部被剔除后 fail-fast。"""
        client = _EmbeddingStubClient(fail_ids={"e1"})
        service = _embed_service(
            [_dim_target("e1", 768), _dim_target("e2", None)], client
        )

        async def scenario():
            return await service.embed("hi", model_id="e1", allow_fallback=True)

        with caplog.at_level("WARNING"):
            with pytest.raises(RoutingExecutionError, match="e1"):
                _run(scenario())
        assert client.calls == ["e1"]  # e2（未声明维度）被剔除，未被尝试
        assert any(
            "维度不匹配" in r.message and "e2" in r.message for r in caplog.records
        )

    def test_no_filter_when_specified_model_declares_no_dimension(self):
        """指定模型未声明 dimension（None）→ 无法校验，不过滤任何候选。"""
        client = _EmbeddingStubClient(fail_ids={"e1"})
        service = _embed_service(
            [_dim_target("e1", None), _dim_target("e2", 1024), _dim_target("e3", 768)],
            client,
        )

        async def scenario():
            return await service.embed("hi", model_id="e1", allow_fallback=True)

        assert _run(scenario()) == [1.0]  # e2 接管（未过滤）
        assert client.calls == ["e1", "e2"]

    def test_default_routing_not_filtered(self):
        """默认路由（model_id=None）不做维度过滤——守卫只管降级场景（11.2 范围）。

        e1（768）失败后必须尝试 e2（1024）：若默认路由也被过滤，
        e2 会被剔除、只可能尝试 e3，断言即失败。
        """
        client = _EmbeddingStubClient(fail_ids={"e1"})
        service = _embed_service(
            [_dim_target("e1", 768), _dim_target("e2", 1024), _dim_target("e3", 768)],
            client,
        )

        async def scenario():
            return await service.embed("hi")

        assert _run(scenario()) == [1.0]
        assert client.calls == ["e1", "e2"]  # 异维度 e2 被尝试 → 证明未过滤

    def test_batch_shares_the_same_guard(self):
        """embed_batch 与 embed 共用 _fallback_targets，守卫同样生效。"""
        client = _EmbeddingStubClient(fail_ids={"e1"})
        service = _embed_service(
            [_dim_target("e1", 768), _dim_target("e2", 1024), _dim_target("e3", 768)],
            client,
        )

        async def scenario():
            return await service.embed_batch(["a", "b"], model_id="e1", allow_fallback=True)

        assert _run(scenario()) == [[1.0], [1.0]]
        assert client.calls == ["e1", "e3"]


# ==================== 配套：dimension 配置类型加固 ====================


class TestDimensionConfigCoercion:
    def test_string_dimension_coerced_to_int(self):
        """YAML 里 dimension 写成字符串（"768"）→ 加载时强转 int（守卫 == 比较的安全前提）。"""
        from core.llm.config.config import load_config_from_dict

        config = load_config_from_dict(
            {"ai": {"embedding": {"candidates": [
                {"id": "e1", "provider": "p1", "model": "m", "dimension": "768"}
            ]}}}
        )
        assert config.embedding.candidates[0].dimension == 768
        assert isinstance(config.embedding.candidates[0].dimension, int)

    def test_invalid_dimension_fails_fast(self):
        """dimension 非数字（如 "abc"）→ 加载期抛 ValueError（fail-fast，不留运行期暗雷）。"""
        from core.llm.config.config import load_config_from_dict

        with pytest.raises(ValueError, match="dimension"):
            load_config_from_dict(
                {"ai": {"embedding": {"candidates": [
                    {"id": "e1", "provider": "p1", "model": "m", "dimension": "abc"}
                ]}}}
            )


# ==================== 11.3 结构化降级日志 ====================


class TestExecutorStructuredLogs:
    def test_failure_log_carries_structured_fields(self, caplog):
        """executor 失败日志携带 6 个结构化字段（extra），原有人读消息不变。"""
        executor = RoutingExecutor(ModelHealthStore(2, 30000))
        t1, t2 = _dim_target("m1", 768), _dim_target("m2", 768)

        async def caller(client, target):
            if target.id == "m1":
                raise RuntimeError("boom-m1")
            return "ok"

        with caplog.at_level("WARNING"):
            result = _run(
                executor.execute_with_fallback(
                    "Embedding", [t1, t2], lambda t: object(), caller
                )
            )
        assert result == "ok"
        rec = next(r for r in caplog.records if "fallback to next" in r.message)
        assert getattr(rec, "capability", None) == "Embedding"
        assert getattr(rec, "failed_model_id", None) == "m1"
        assert getattr(rec, "failed_provider", None) == "p1"
        assert getattr(rec, "error_type", None) == "RuntimeError"
        assert getattr(rec, "error_message", None) == "boom-m1"
        assert getattr(rec, "fallback_count", None) == 0  # 首个候选失败

    def test_fallback_count_increments_per_failure(self, caplog):
        """连续失败时 fallback_count 递增（= 已发生的降级次数）。"""
        executor = RoutingExecutor(ModelHealthStore(5, 30000))
        t1, t2 = _dim_target("m1", 768), _dim_target("m2", 768)

        async def caller(client, target):
            raise RuntimeError(f"boom-{target.id}")

        with caplog.at_level("WARNING"):
            with pytest.raises(RoutingExecutionError):
                _run(
                    executor.execute_with_fallback(
                        "Embedding", [t1, t2], lambda t: object(), caller
                    )
                )
        counts = [
            getattr(r, "fallback_count")
            for r in caplog.records
            if "fallback to next" in r.message
        ]
        assert counts == [0, 1]

    def test_fallback_count_excludes_skipped_candidates(self, caplog):
        """被跳过的候选（熔断中，allow_call 拒绝）不计入 fallback_count：
        m1 熔断 → m2 失败的日志 fallback_count 仍为 0。"""
        store = ModelHealthStore(5, 30000)
        health = ModelHealth()
        health.state = HealthState.OPEN
        health.open_until = float("inf")  # 熔断中
        store.health_by_id["m1"] = health

        executor = RoutingExecutor(store)
        t1, t2 = _dim_target("m1", 768), _dim_target("m2", 768)

        async def caller(client, target):
            raise RuntimeError(f"boom-{target.id}")

        with caplog.at_level("WARNING"):
            with pytest.raises(RoutingExecutionError):
                _run(
                    executor.execute_with_fallback(
                        "Embedding", [t1, t2], lambda t: object(), caller
                    )
                )
        rec = next(r for r in caplog.records if "m2" in r.message)
        assert getattr(rec, "fallback_count", None) == 0  # m1 被跳过，未计入


class TestStreamStructuredLogs:
    def _service(self, first_mode: str):
        """两候选流式服务：p1/m1 按 first_mode 表现，p2/m2 正常完成。"""
        return RoutingLLMService(
            selector=_ChatSelectorStub(
                [_dim_target("m1", None, "p1"), _dim_target("m2", None, "p2")]
            ),
            health_store=ModelHealthStore(5, 30000),
            executor=RoutingExecutor(ModelHealthStore(5, 30000)),
            clients=[_StreamStubClient("p1", first_mode), _StreamStubClient("p2", "ok")],
        )

    def test_pre_first_packet_failure_log_structured(self, caplog):
        """首包前失败（路径 B）日志携带结构化字段。"""
        service = self._service("error")
        request = ChatRequest(messages=[Message.user("hi")])

        with caplog.at_level("WARNING"):
            _run(service.stream_chat(request, BaseStreamCallback()))
        rec = next(
            r for r in caplog.records if "fallback to next" in r.message
        )
        assert getattr(rec, "capability", None) == "Chat"
        assert getattr(rec, "failed_model_id", None) == "m1"
        assert getattr(rec, "failed_provider", None) == "p1"
        assert getattr(rec, "result", None) == "error"
        assert getattr(rec, "error_type", None) == "RuntimeError"

    def test_after_first_packet_failure_log_structured(self, caplog):
        """首包后失败（路径 A 尾段）日志携带结构化字段。"""
        service = self._service("midstream")
        request = ChatRequest(messages=[Message.user("hi")])

        with caplog.at_level("WARNING"):
            _run(service.stream_chat(request, BaseStreamCallback()))
        rec = next(
            r for r in caplog.records if "failed after first packet" in r.message
        )
        assert getattr(rec, "capability", None) == "Chat"
        assert getattr(rec, "failed_model_id", None) == "m1"
        assert getattr(rec, "result", None) == "error"
        assert getattr(rec, "error_type", None) == "RuntimeError"
