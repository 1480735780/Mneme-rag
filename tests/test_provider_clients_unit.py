# -*- coding: utf-8 -*-
"""
P0 provider 装配单测（Ollama/SiliconFlow/AIHubMix chat + Qwen/OpenAI embedding）

本地 httpx.MockTransport 桩验证（不对接真实后端）：
    - 各客户端 provider 标识与 requires_api_key 语义
    - Ollama：无 Authorization 头、不注入 enable_thinking、chat/stream_chat 可用
    - SiliconFlow / AIHubMix：带 Bearer key 的 chat 调用
    - 缺 API key → ModelClientException UNAUTHORIZED
    - Qwen / OpenAI embedding：embed / embed_batch 请求体与响应解析
    - wiring._build_chat_clients：ollama 空 key 放行、其余占位符跳过
"""
import asyncio
import json

import httpx
import pytest

from app.wiring import _build_chat_clients
from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from core.llm.callback import BaseStreamCallback
from core.llm.config.config import AIModelConfig, ModelCandidate, ProviderConfig
from core.llm.model.model_target import ModelTarget
from core.llm.providers.aihubmix import AIHubMixChatClient
from core.llm.providers.ollama import OllamaChatClient
from core.llm.providers.openai_embedding import OpenAIEmbeddingClient
from core.llm.providers.qwen_embedding import QwenEmbeddingClient
from core.llm.providers.siliconflow import SiliconFlowChatClient
from core.llm.providers.siliconflow_embedding import SiliconFlowEmbeddingClient
from core.llm.schema import ChatRequest, Message, Role


def _provider_config(api_key="test-key"):
    return ProviderConfig(
        url="https://example.com",
        api_key=api_key,
        endpoints={"chat": "/v1/chat/completions", "embedding": "/v1/embeddings"},
    )


def _chat_target(provider: str, api_key="test-key"):
    candidate = ModelCandidate(id=f"{provider}-m", provider=provider, model="test-model")
    return ModelTarget(id=candidate.id, candidate=candidate, provider=_provider_config(api_key))


def _embed_target(provider: str, api_key="test-key", dimension=768):
    candidate = ModelCandidate(
        id=f"{provider}-e", provider=provider, model="text-embed", dimension=dimension
    )
    return ModelTarget(id=candidate.id, candidate=candidate, provider=_provider_config(api_key))


def _chat_request() -> ChatRequest:
    return ChatRequest(messages=[Message(role=Role.USER, content="你好")], temperature=0.7)


def _make_client(cls, handler):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return cls(http_client=http_client)


def _capture_handler(captured, payload, status=200):
    """记录请求的 handler；响应固定 JSON payload。"""

    async def handler(request):
        captured.append(request)
        return httpx.Response(status, json=payload)

    return handler


@pytest.mark.parametrize("cls,provider", [
    (OllamaChatClient, "ollama"),
    (SiliconFlowChatClient, "siliconflow"),
    (AIHubMixChatClient, "aihubmix"),
])
def test_chat_client_provider(cls, provider):
    assert cls().provider == provider


@pytest.mark.parametrize("cls,provider", [
    (QwenEmbeddingClient, "qwen"),
    (OpenAIEmbeddingClient, "openai"),
])
def test_embed_client_provider(cls, provider):
    assert cls().provider == provider


def test_ollama_requires_no_api_key():
    assert OllamaChatClient().requires_api_key() is False
    assert SiliconFlowChatClient().requires_api_key() is True
    assert AIHubMixChatClient().requires_api_key() is True


def test_ollama_chat_no_auth_no_thinking():
    async def run():
        captured = []
        client = _make_client(OllamaChatClient, _capture_handler(captured, {
            "choices": [{"message": {"content": "你好世界"}}],
        }))
        try:
            result = await client.chat(_chat_request(), _chat_target("ollama", api_key=""))
            assert result == "你好世界"
            req = captured[0]
            assert "authorization" not in req.headers
            body = json.loads(req.content)
            assert "enable_thinking" not in body
            assert body["model"] == "test-model"
            assert body["messages"] == [{"role": "user", "content": "你好"}]
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_siliconflow_chat_with_bearer_key():
    async def run():
        captured = []
        client = _make_client(SiliconFlowChatClient, _capture_handler(captured, {
            "choices": [{"message": {"content": "ok"}}],
        }))
        try:
            result = await client.chat(_chat_request(), _chat_target("siliconflow"))
            assert result == "ok"
            assert captured[0].headers["authorization"] == "Bearer test-key"
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_aihubmix_chat():
    async def run():
        captured = []
        client = _make_client(AIHubMixChatClient, _capture_handler(captured, {
            "choices": [{"message": {"content": "hi"}}],
        }))
        try:
            result = await client.chat(_chat_request(), _chat_target("aihubmix"))
            assert result == "hi"
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_chat_missing_api_key_raises_unauthorized():
    async def run():
        client = SiliconFlowChatClient(http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(_capture_handler([], {}))
        ))
        try:
            with pytest.raises(ModelClientException) as exc:
                await client.chat(_chat_request(), _chat_target("siliconflow", api_key=""))
            assert exc.value.error_type == ModelClientErrorType.UNAUTHORIZED
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_ollama_stream_chat():
    async def run():
        captured = []
        sse = (
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )

        async def handler(request):
            captured.append(request)
            return httpx.Response(200, text=sse)

        client = _make_client(OllamaChatClient, handler)

        class Rec(BaseStreamCallback):
            def __init__(self):
                self.chunks = []
                self.completed = False
                self.error = None

            async def on_content(self, token):
                self.chunks.append(token)

            async def on_complete(self):
                self.completed = True

            async def on_error(self, error):
                self.error = error

        try:
            rec = Rec()
            await client.stream_chat(_chat_request(), rec, _chat_target("ollama", api_key=""))
            assert rec.chunks == ["你", "好"]
            assert rec.completed is True
            assert rec.error is None
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_qwen_embedding():
    async def run():
        captured = []
        client = _make_client(QwenEmbeddingClient, _capture_handler(captured, {
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
        }))
        try:
            vec = await client.embed("文本", _embed_target("qwen"))
            assert vec == [0.1, 0.2, 0.3]
            body = json.loads(captured[0].content)
            assert body["model"] == "text-embed"
            assert body["input"] == ["文本"]
            assert body["dimensions"] == 768
            assert body["encoding_format"] == "float"
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_openai_embedding_batch():
    async def run():
        captured = []
        client = _make_client(OpenAIEmbeddingClient, _capture_handler(captured, {
            "data": [{"embedding": [1.0, 2.0]}, {"embedding": [3.0, 4.0]}],
        }))
        try:
            vecs = await client.embed_batch(["a", "b"], _embed_target("openai"))
            assert vecs == [[1.0, 2.0], [3.0, 4.0]]
            body = json.loads(captured[0].content)
            assert body["input"] == ["a", "b"]
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_embedding_max_batch_size():
    # P3-1：百炼（qwen/DashScope compatible-mode）批量上限 10，对齐 Java BaiLianEmbeddingClient
    assert QwenEmbeddingClient().max_batch_size() == 10
    # 其余 provider 对齐 Java：siliconflow 32 / openai 默认 0（不限制）
    assert SiliconFlowEmbeddingClient().max_batch_size() == 32
    assert OpenAIEmbeddingClient().max_batch_size() == 0


def test_qwen_embedding_batch_sharding():
    """P3-1：25 条 → 10/10/5 三片，每片一次 HTTP，结果按输入序回填（对齐 Java embedBatch 分片）。"""

    captured = []

    async def handler(request):
        # 既捕获请求，又按本片条数动态回包（向量首元素 = 本片条数，供回填校验）
        captured.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"embedding": [float(len(body["input"])), 0.0]} for _ in body["input"]]})

    async def run():
        client = QwenEmbeddingClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            texts = [f"t{i}" for i in range(25)]
            vecs = await client.embed_batch(texts, _embed_target("qwen"))
            # 三片请求的 input 尺寸 10/10/5
            sizes = [len(json.loads(req.content)["input"]) for req in captured]
            assert sizes == [10, 10, 5]
            # 结果与输入等长且按序回填（首向量标注所属片大小 10/10/5）
            assert len(vecs) == 25
            assert vecs[0][0] == 10.0 and vecs[9][0] == 10.0
            assert vecs[10][0] == 10.0 and vecs[19][0] == 10.0
            assert vecs[20][0] == 5.0 and vecs[24][0] == 5.0
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_qwen_embedding_batch_within_limit_single_call():
    """P3-1：≤ 上限（10）不拆片，单次请求（对齐 Java：texts.size() <= batch 走单次）。"""

    async def run():
        captured = []
        client = _make_client(QwenEmbeddingClient, _capture_handler(captured, {
            "data": [{"embedding": [1.0]} for _ in range(10)],
        }))
        try:
            vecs = await client.embed_batch([f"t{i}" for i in range(10)], _embed_target("qwen"))
            assert len(captured) == 1
            assert len(vecs) == 10
        finally:
            await client._http_client.aclose()

    asyncio.run(run())


def test_build_chat_clients_ollama_allowed_without_key():
    config = AIModelConfig(providers={
        "qwen": ProviderConfig(url="https://x", api_key="real-key"),
        "ollama": ProviderConfig(
            url="http://localhost:11434",
            api_key="",
            endpoints={"chat": "/v1/chat/completions"},
        ),
        "siliconflow": ProviderConfig(url="https://x", api_key="${SILICONFLOW_API_KEY}"),
        "aihubmix": ProviderConfig(url="https://x", api_key="${AIHUBMIX_API_KEY}"),
    })
    clients = _build_chat_clients(config)
    providers = {c.provider for c in clients}
    assert providers == {"qwen", "ollama"}
