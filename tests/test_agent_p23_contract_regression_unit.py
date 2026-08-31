# -*- coding: utf-8 -*-
"""
P2/P3 收官回归防线：前后端联调契约 + 批量 embedding 边界（v1.1 对齐收官）

    P2 联调契约（controller 层，TestClient 直连 ASGI）：
        - 七类帧全谱（现有 controller 测试只锁 meta/message/finish 三类；tool/hint/cancel/done
          四类帧从未在 HTTP 层验证——前端 sse.ts 七类 dispatch 却只测三类，契约单侧裸奔）
        - done 帧载荷为纯文本 [DONE]（前端 handleFrame 的 JSON.parse 失败分支按原文分发）
        - SSE 防 buffer 响应头（Cache-Control no-cache / X-Accel-Buffering no——
          Nginx/vite 反代下流式输出不被缓冲的联调验收点，见 docker/DEPLOY.md）
        - 含换行 delta 的多行 data: 拆行往返（encode_event 按 SSE 规范拆行，
          前端 dataLines.join("\\n") 恢复——真实 LLM 流式 markdown 输出的常态）
        - cancel 流帧序（cancel + done）与 cancel 载荷 camelCase
    P3-1 批量 embedding 边界（百炼上限 10 适配的分片器 + 路由层贯通）：
        - 空列表零请求；整除边界（20 → 恰两片不越界）
        - 分片中途失败 fail-fast（不吞错、不返回部分结果）
        - 路由层贯通：RoutingEmbeddingService 两条路径都必须走 client.embed_batch
          （若有人把路由层退化成逐条 embed，百炼批量上限适配即失效）
"""
import asyncio
import json
from typing import List, Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.config import AgentProperties
from agent.models import (
    AgentCompletionPayload,
    AgentHintPayload,
    AgentMessageDelta,
    AgentMetaPayload,
    AgentToolProgress,
)
from agent.run_gate import AgentRunGate
from agent.service import AgentConversationService, AgentSseSender
from agent.state_store import PgAgentStateStore
from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from common.exception.model_client_exception import ModelClientException
from core.llm.config.config import ModelCandidate, ProviderConfig
from core.llm.embedding import RoutingEmbeddingService
from core.llm.model.model_target import ModelTarget
from core.llm.providers.base_embedding import BaseEmbeddingClient
from core.llm.providers.qwen_embedding import QwenEmbeddingClient
from storage.cache import MemoryCacheManager
from storage.database import InMemoryDatabaseClient


# ==================== P2 联调契约：桩件 ====================


class _FullSpectrumChatService:
    """依次发出全部七类帧（真实 Agent 一次成功运行的完整事件谱）"""

    async def stream_chat(self, question, user_id, conversation_id, sender: AgentSseSender):
        sender.send_event("meta", AgentMetaPayload("c1", "t-1"))
        sender.send_event("message", AgentMessageDelta(type="think", delta="先查一下"))
        sender.send_event("message", AgentMessageDelta(type="response", delta="答案"))
        sender.send_event(
            "tool",
            AgentToolProgress(name="search_knowledge", display_name="知识库检索", status="start"),
        )
        sender.send_event(
            "tool",
            AgentToolProgress(
                name="search_knowledge", display_name="知识库检索", status="end", result="命中 3 条", ok=True
            ),
        )
        sender.send_event("hint", AgentHintPayload(code="MAX_ITERATIONS", text="已达到迭代上限"))
        sender.send_event(
            "finish", AgentCompletionPayload(message_id="m-1", title="标题", message_status="NORMAL")
        )
        sender.send_event("done", "[DONE]")
        sender.complete()

    async def stop_task(self, task_id):
        pass


class _CancelledChatService:
    """用户取消流：cancel（AgentCompletionPayload）+ done（[DONE]）收尾"""

    async def stream_chat(self, question, user_id, conversation_id, sender: AgentSseSender):
        sender.send_event("meta", AgentMetaPayload("c1", "t-1"))
        sender.send_event("message", AgentMessageDelta(type="response", delta="已生成部分"))
        sender.send_event(
            "cancel",
            AgentCompletionPayload(message_id="m-1", title="标题", message_status="INTERRUPTED"),
        )
        sender.send_event("done", "[DONE]")
        sender.complete()

    async def stop_task(self, task_id):
        pass


class _MultilineChatService:
    """含真实换行符的原始载荷（send_raw 路径）+ JSON 转义换行的 delta（send_event 路径）

    JSON 载荷经 json.dumps 后换行已转义为 \\n 两字符，不触发 encode_event 拆行；
    真正触发拆行的是 send_raw 直接发含真实换行的文本——两条路径都锁。
    """

    async def stream_chat(self, question, user_id, conversation_id, sender: AgentSseSender):
        sender.send_event("meta", AgentMetaPayload("c1", "t-1"))
        # 路径一：JSON 载荷里的换行（json.dumps 转义，单行 data）
        sender.send_event("message", AgentMessageDelta(type="response", delta="第一行\n第二行\n\n第四行"))
        # 路径二：原始载荷里的真实换行（encode_event 按 SSE 规范拆多行 data:）
        sender.send_raw("error", "错误详情第一行\n错误详情第二行")
        sender.send_event("finish", AgentCompletionPayload(message_id="m-1", title="t", message_status="NORMAL"))
        sender.send_event("done", "[DONE]")
        sender.complete()

    async def stop_task(self, task_id):
        pass


def _wire_with(service):
    def install(self):
        self.agent_engine_properties = AgentProperties(chat_provider="siliconflow", chat_model="m")
        self.agent_engine_conversation_service = AgentConversationService(
            self.db, PgAgentStateStore(self.db), AgentRunGate(MemoryCacheManager(), 1000)
        )
        self.agent_engine_chat_service = service

    return install


def _app(monkeypatch, service) -> FastAPI:
    monkeypatch.setattr(AppContainer, "_wire_agent_engine", _wire_with(service))
    return create_app(AppSettings(stack_profile="memory"))


def _frontend_parse(text: str):
    """前端 sse.ts handleFrame 的同款解析规则（联调契约的裁判方）

    event: 前缀取事件名 / data: 前缀逐行收集 join("\\n") / "\\n\\n" 分帧 / data 行内容 trim。
    """
    frames = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if not data_lines:
            continue
        frames.append((event, "\n".join(data_lines)))
    return frames


# ==================== P2：七类帧全谱契约 ====================


class TestFullFrameSpectrumContract:
    def test_all_seven_frame_types_frontend_parseable(self, monkeypatch):
        """七类帧全谱经 HTTP 传输后，前端解析规则逐帧可读且载荷键与 types.ts 逐字段一致。

        现有 controller 测试只锁 meta/message/finish——tool/hint/cancel/done 四类帧的
        契约此前完全靠前端单侧假设。任一 payload 字段改名（如 displayName 回退
        display_name）前端静默拿 undefined，工具进度条/提示条直接断头。
        """
        with TestClient(_app(monkeypatch, _FullSpectrumChatService())) as client:
            resp = client.get("/agent/v1/chat", params={"question": "q"})
            assert resp.status_code == 200
            frames = _frontend_parse(resp.text)
            assert [e for e, _ in frames] == [
                "meta", "message", "message", "tool", "tool", "hint", "finish", "done",
            ]
            by_event = {}
            for event, data in frames:
                by_event.setdefault(event, []).append(data)

            assert json.loads(by_event["meta"][0]) == {"conversationId": "c1", "taskId": "t-1"}
            assert json.loads(by_event["message"][0]) == {"type": "think", "delta": "先查一下"}
            assert json.loads(by_event["message"][1]) == {"type": "response", "delta": "答案"}
            # tool start/end 两帧：status 枚举与前端 AgentToolProgress 收窄类型一致
            assert json.loads(by_event["tool"][0]) == {
                "name": "search_knowledge", "displayName": "知识库检索", "status": "start",
            }
            assert json.loads(by_event["tool"][1]) == {
                "name": "search_knowledge", "displayName": "知识库检索",
                "status": "end", "result": "命中 3 条", "ok": True,
            }
            assert json.loads(by_event["hint"][0]) == {"code": "MAX_ITERATIONS", "text": "已达到迭代上限"}
            assert json.loads(by_event["finish"][0]) == {
                "messageId": "m-1", "title": "标题", "messageStatus": "NORMAL",
            }
            # done 帧载荷为纯文本 [DONE]：前端 JSON.parse 失败 → 按原文分发 → onDone 不消费载荷
            assert by_event["done"][0] == "[DONE]"

    def test_cancel_stream_frames_contract(self, monkeypatch):
        """取消流收尾帧序 cancel → done；cancel 载荷 = AgentCompletionPayload（camelCase）。

        前端 onCancel 以 messageStatus=INTERRUPTED 把气泡转取消态并保留已生成部分——
        字段改名会让取消后的消息在回放里显示为正常完成。
        """
        with TestClient(_app(monkeypatch, _CancelledChatService())) as client:
            resp = client.get("/agent/v1/chat", params={"question": "q"})
            frames = _frontend_parse(resp.text)
            assert [e for e, _ in frames][-2:] == ["cancel", "done"]
            cancel = json.loads(frames[-2][1])
            assert cancel == {"messageId": "m-1", "title": "标题", "messageStatus": "INTERRUPTED"}

    def test_sse_anti_buffer_headers(self, monkeypatch):
        """SSE 响应必须带防代理缓冲头——Nginx/vite 反代下缺 X-Accel-Buffering: no
        会让流式输出被缓冲成一次性大块（前端打字机效果消失），联调时极难定位。"""
        with TestClient(_app(monkeypatch, _FullSpectrumChatService())) as client:
            resp = client.get("/agent/v1/chat", params={"question": "q"})
            assert resp.headers["cache-control"] == "no-cache"
            assert resp.headers["x-accel-buffering"] == "no"
            assert "text/event-stream" in resp.headers["content-type"]

    def test_multiline_payload_roundtrip(self, monkeypatch):
        """换行载荷的两条往返路径：

        JSON 载荷（json.dumps 转义换行 → 单行 data）与原始载荷（encode_event 按
        SSE 规范拆多行 data: → 前端 dataLines.join("\\n") 恢复）。前端解析规则
        任何一侧与后端编码不一致，换行会丢（段落粘连）或残留（每行重复前缀）。
        """
        with TestClient(_app(monkeypatch, _MultilineChatService())) as client:
            resp = client.get("/agent/v1/chat", params={"question": "q"})
            frames = _frontend_parse(resp.text)
            assert [e for e, _ in frames] == ["meta", "message", "error", "finish", "done"]
            # 路径一：JSON 转义换行原样往返
            delta = json.loads(frames[1][1])
            assert delta["delta"] == "第一行\n第二行\n\n第四行"
            # 路径二：真实换行拆多行 data: 后按前端规则拼回原文
            assert frames[2][1] == "错误详情第一行\n错误详情第二行"


# ==================== P3-1：批量 embedding 分片边界 ====================


def _embed_target(provider: str = "qwen", api_key="test-key"):
    candidate = ModelCandidate(id=f"{provider}-e", provider=provider, model="text-embed", dimension=768)
    return ModelTarget(
        id=candidate.id,
        candidate=candidate,
        provider=ProviderConfig(
            url="https://example.com",
            api_key=api_key,
            endpoints={"chat": "/v1/chat/completions", "embedding": "/v1/embeddings"},
        ),
    )


def _run(coro):
    return asyncio.run(coro)


class TestEmbedBatchBoundaries:
    def test_empty_input_returns_empty_without_request(self):
        """空列表 → 空结果且零 HTTP 请求（意图分类空树等调用方的常态输入）。"""

        captured = []

        async def handler(request):
            captured.append(request)
            return httpx.Response(200, json={"data": []})

        async def scenario():
            client = QwenEmbeddingClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
            try:
                vecs = await client.embed_batch([], _embed_target())
                return vecs
            finally:
                await client._http_client.aclose()

        assert _run(scenario()) == []
        assert captured == []

    def test_exact_multiple_boundary_no_extra_request(self):
        """整除边界：20 条恰两片 10/10——分片循环步进不得越界多发空片或重片。"""

        captured = []

        async def handler(request):
            captured.append(request)
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"data": [{"embedding": [float(len(body["input"]))]} for _ in body["input"]]}
            )

        async def scenario():
            client = QwenEmbeddingClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
            try:
                return await client.embed_batch([f"t{i}" for i in range(20)], _embed_target())
            finally:
                await client._http_client.aclose()

        vecs = _run(scenario())
        sizes = [len(json.loads(req.content)["input"]) for req in captured]
        assert sizes == [10, 10]  # 恰两片，无第三片
        assert len(vecs) == 20
        assert all(v[0] == 10.0 for v in vecs)  # 每片都满 10 条，回填无错位

    def test_midway_shard_failure_fails_fast(self):
        """分片中途失败（第二片 HTTP 400）→ 整批抛 ModelClientException。

        语义锁定：fail-fast 不吞错、不返回部分结果——调用方（入库向量化/意图批量
        分类）必须感知整批失败，半批向量落库会造成静默数据缺失。
        """

        calls = {"n": 0}

        async def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                body = json.loads(request.content)
                return httpx.Response(
                    200, json={"data": [{"embedding": [1.0]} for _ in body["input"]]}
                )
            return httpx.Response(400, json={"error": {"code": "InvalidParameter", "message": "batch too large"}})

        async def scenario():
            client = QwenEmbeddingClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
            try:
                await client.embed_batch([f"t{i}" for i in range(25)], _embed_target())
                return None
            except ModelClientException as e:
                return e
            finally:
                await client._http_client.aclose()

        err = _run(scenario())
        assert isinstance(err, ModelClientException)  # 不吞错
        assert calls["n"] == 2  # 第一片成功、第二片失败即停（无第三片重试）
        assert "400" in str(err)


# ==================== P3-1：路由层批量贯通 ====================


class _RecordingClient(BaseEmbeddingClient):
    """记录 embed / embed_batch 调用形态的桩客户端（provider=qwen → 批量上限 10 的角色）"""

    provider = "qwen"

    def __init__(self):
        self.embed_calls = 0
        self.batch_calls: List[int] = []  # 每次批量调用的条数

    async def embed(self, text, target):
        self.embed_calls += 1
        return [1.0]

    async def embed_batch(self, texts, target):
        self.batch_calls.append(len(texts))
        return [[1.0] for _ in texts]


class _SelectorStub:
    def __init__(self, target):
        self._target = target

    def select_embedding_candidates(self):
        return [self._target]


class _ExecutorStub:
    """故障转移桩：取首个候选直调（与真实 executor 的首候选成功路径等价）"""

    async def execute_with_fallback(self, capability, targets, resolve_client, fn):
        target = targets[0]
        client = resolve_client(target)
        return await fn(client, target)


class TestRoutingBatchPassThrough:
    def test_routing_embed_batch_uses_client_batch_not_per_item(self):
        """路由层两条路径（指定 model_id 直连 / 未指定走 executor）都必须走 client.embed_batch。

        P3-1 的百炼批量上限适配在 client 层（max_batch_size=10 分片）——若有人把
        RoutingEmbeddingService.embed_batch 改成循环调单条 embed，25 条会退化成
        25 次单条请求（分片、保序、批量 RPC 优化全部失效），且单测无感。
        """
        target = _embed_target()
        client = _RecordingClient()
        service = RoutingEmbeddingService(
            selector=_SelectorStub(target), executor=_ExecutorStub(), clients=[client]
        )

        async def scenario():
            direct = await service.embed_batch([f"t{i}" for i in range(25)], model_id=target.id)
            routed = await service.embed_batch([f"u{i}" for i in range(25)])
            return direct, routed

        direct, routed = _run(scenario())
        # 两条路径都走了批量接口：各 1 次调用、传入全量 25 条（分片由 client 内部承接）
        assert client.batch_calls == [25, 25]
        assert client.embed_calls == 0  # 一次单条 embed 都不许出现
        assert len(direct) == 25 and len(routed) == 25

    def test_routing_embed_batch_unknown_model_rejected(self):
        """指定不存在 model_id → RoutingExecutionError fail-fast（对齐 Java 直连不降级语义）。"""
        from core.llm.model.routing_executor import RoutingExecutionError

        target = _embed_target()
        service = RoutingEmbeddingService(
            selector=_SelectorStub(target), executor=_ExecutorStub(), clients=[_RecordingClient()]
        )

        async def scenario():
            try:
                await service.embed_batch(["t"], model_id="no-such-model")
            except RoutingExecutionError as e:
                return e
            return None

        err = _run(scenario())
        assert isinstance(err, RoutingExecutionError)
        assert "no-such-model" in str(err)
