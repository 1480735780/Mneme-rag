# -*- coding: utf-8 -*-
"""
v1.1 P2 Agent 引擎端点测试：/agent/v1/chat | /agent/v1/stop | /agent/v1/conversations* | /agent/v1/meta
（对应 Java AgentChatController / AgentConversationController / AgentMetaController）

覆盖：
    - 条件挂载（@ConditionalOnAgentEngine）：显式 workflow 四端点 404 不可达（默认 agent，决策 3B）
    - meta：framework/model/maxIters/capabilities/toolProvider/mcpConfigured camelCase；
      能力清单与 mcpConfigured 同源（mcp 工具数 > 0 才报 mcp-tools）
    - chat SSE：meta/message/finish 帧序 + camelCase 载荷 + user 上下文透传；
      @ChatQuestion 校验（空白 → 问题不能为空；>500 字 → 问题过长，最多 500 字）；
      服务层 ClientException（闸门繁忙）→ CLIENT_ERROR Result
    - stop：code=0 + taskId 透传
    - 会话 CRUD（真实 AgentConversationService + InMemory db）：列表 camelCase + 轮数、
      消息历史字段面、重命名（空标题/不存在 → ClientException Result）、软删、批量软删、用户隔离
"""
import json
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.config import AgentProperties
from agent.models import AgentCompletionPayload, AgentMessageDelta, AgentMessageStatus, AgentMetaPayload
from agent.run_gate import AgentRunGate
from agent.service import AgentConversationService
from agent.state_store import PgAgentStateStore
from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from common.exception.business import ClientException
from storage.cache import MemoryCacheManager


# ==================== 桩件 ====================


class _StubAgentChatService:
    """chat/stop 调用记录器；stream_error 可编程注入异常（如闸门繁忙）"""

    def __init__(self, stream_error: Optional[BaseException] = None):
        self.stream_calls = []
        self.stop_calls = []
        self.stream_error = stream_error

    async def stream_chat(self, question, user_id, conversation_id, sender):
        self.stream_calls.append((question, user_id, conversation_id))
        if self.stream_error is not None:
            raise self.stream_error
        sender.send_event("meta", AgentMetaPayload(conversation_id or "c-gen", "t-1"))
        sender.send_event("message", AgentMessageDelta(type="response", delta="你好"))
        sender.send_event(
            "finish",
            AgentCompletionPayload(message_id="m-1", title="标题", message_status="NORMAL"),
        )
        sender.complete()

    async def stop_task(self, task_id, requester):
        # R-B：requester（UserContext.get_user_id()）随调用透传，供 cancel_by_user 属主复核
        self.stop_calls.append((task_id, requester))


class _StubCatalog:
    def __init__(self, mcp_count: int = 0):
        self._mcp_count = mcp_count

    def mcp_tool_count(self) -> int:
        return self._mcp_count


def _stub_wire(mcp_count: int = 0, stream_error: Optional[BaseException] = None):
    """替换 _wire_agent_engine：stub chat/catalog + 真实会话服务（InMemory db 之上）"""

    def install(self):
        self.agent_engine_properties = AgentProperties(
            chat_provider="siliconflow", chat_model="test-model", max_iters=8
        )
        self.agent_engine_tool_catalog = _StubCatalog(mcp_count=mcp_count)
        self.agent_engine_conversation_service = AgentConversationService(
            self.db, PgAgentStateStore(self.db), AgentRunGate(MemoryCacheManager(), 1000)
        )
        self.agent_engine_chat_service = _StubAgentChatService(stream_error=stream_error)

    return install


def _app(monkeypatch, **kw) -> FastAPI:
    monkeypatch.setattr(AppContainer, "_wire_agent_engine", _stub_wire(**kw))
    return create_app(AppSettings(stack_profile="memory"))


def _frames(text: str):
    """SSE 文本 → [(event, data_json_str)]"""
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = None
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        out.append((event, "\n".join(data_lines)))
    return out


# ==================== 条件挂载 ====================


class TestConditionalMounting:
    def test_workflow_mode_endpoints_404(self, monkeypatch):
        # 决策 3B（2026-08-30）落地后默认 agent；显式 workflow 时引擎域不装配 → 四端点不可达
        # （对齐 @ConditionalOnAgentEngine 的 workflow 分支）
        monkeypatch.setenv("RAGENT_ENGINE_TYPE", "workflow")
        app = create_app(AppSettings(stack_profile="memory"))
        with TestClient(app) as client:
            assert client.get("/agent/v1/meta").status_code == 404
            assert client.get("/agent/v1/chat", params={"question": "q"}).status_code == 404
            assert client.post("/agent/v1/stop", params={"task_id": "t"}).status_code == 404
            assert client.get("/agent/v1/conversations").status_code == 404


# ==================== meta ====================


class TestMetaEndpoint:
    def test_meta_native(self, monkeypatch):
        with TestClient(_app(monkeypatch, mcp_count=0)) as client:
            resp = client.get("/agent/v1/meta")
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == "0"
            assert body["data"] == {
                "framework": "AgentScope ReAct",
                "model": "test-model",
                "maxIters": 8,
                "capabilities": ["react", "knowledge-base"],
                "toolProvider": "native",
                "mcpConfigured": False,
            }

    def test_meta_with_mcp_tools(self, monkeypatch):
        with TestClient(_app(monkeypatch, mcp_count=3)) as client:
            data = client.get("/agent/v1/meta").json()["data"]
            assert data["mcpConfigured"] is True
            assert data["toolProvider"] == "native + mcp"
            assert data["capabilities"] == ["react", "knowledge-base", "mcp-tools"]


# ==================== chat SSE / stop ====================


class TestChatEndpoint:
    def test_chat_sse_frames(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            resp = client.get(
                "/agent/v1/chat",
                params={"question": "你好", "conversation_id": "c1"},
                headers={"X-User-Id": "u1"},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            frames = _frames(resp.text)
            assert [event for event, _ in frames] == ["meta", "message", "finish"]
            assert json.loads(frames[0][1]) == {"conversationId": "c1", "taskId": "t-1"}
            assert json.loads(frames[1][1]) == {"type": "response", "delta": "你好"}
            assert json.loads(frames[2][1]) == {
                "messageId": "m-1", "title": "标题", "messageStatus": "NORMAL",
            }
            # 服务侧：question/user_id/conversation_id 透传（snake_case query 参数）
            chat = client.app.state.container.agent_engine_chat_service
            assert chat.stream_calls == [("你好", "u1", "c1")]

    def test_chat_blank_question_rejected(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            body = client.get("/agent/v1/chat", params={"question": "   "}).json()
            assert body["code"] != "0"
            assert body["message"] == "问题不能为空"

    def test_chat_overlong_question_rejected(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            body = client.get("/agent/v1/chat", params={"question": "问" * 501}).json()
            assert body["code"] != "0"
            assert body["message"] == "问题过长，最多 500 字"

    def test_chat_boundary_length_accepted(self, monkeypatch):
        # 恰 500 字通过校验（@ChatQuestion.Size(max=500) 上限含等号）
        with TestClient(_app(monkeypatch)) as client:
            resp = client.get("/agent/v1/chat", params={"question": "问" * 500})
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

    def test_chat_busy_gate_returns_client_error(self, monkeypatch):
        err = ClientException("当前会话处理中，请稍后再发起新的对话")
        with TestClient(_app(monkeypatch, stream_error=err)) as client:
            body = client.get("/agent/v1/chat", params={"question": "q"}).json()
            assert body["code"] != "0"
            assert body["message"] == "当前会话处理中，请稍后再发起新的对话"

    def test_stop_task(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            resp = client.post(
                "/agent/v1/stop", params={"task_id": "t-9"}, headers={"X-User-Id": "u1"},
            )
            assert resp.status_code == 200
            assert resp.json()["code"] == "0"
            chat = client.app.state.container.agent_engine_chat_service
            assert chat.stop_calls == [("t-9", "u1")]  # X-User-Id: u1 → 发起方透传


# ==================== 会话 CRUD ====================


class TestConversationEndpoints:
    def _seed(self, container):
        svc = container.agent_engine_conversation_service
        svc.touch_conversation("c1", "u1", "第一问")
        svc.add_user_message("c1", "u1", "第一问")
        svc.add_assistant_message(
            "c1", "u1", "回答正文", "思考过程",
            [{"kind": "answer", "text": "回答正文"}],
            None, AgentMessageStatus.NORMAL,
        )

    def test_list_conversations_camelcase(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            self._seed(client.app.state.container)
            resp = client.get("/agent/v1/conversations", headers={"X-User-Id": "u1"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert len(data) == 1
            row = data[0]
            assert set(row) == {"conversationId", "title", "lastTime", "turns"}
            assert row["conversationId"] == "c1"
            assert row["title"] == "第一问"
            assert row["turns"] == 1

    def test_list_user_isolation(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            self._seed(client.app.state.container)
            resp = client.get("/agent/v1/conversations", headers={"X-User-Id": "u2"})
            assert resp.json()["data"] == []

    def test_list_messages_field_shape(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            self._seed(client.app.state.container)
            resp = client.get(
                "/agent/v1/conversations/c1/messages", headers={"X-User-Id": "u1"}
            )
            rows = resp.json()["data"]
            assert [r["role"] for r in rows] == ["user", "assistant"]
            assistant = rows[1]
            assert set(assistant) == {
                "id", "role", "content", "thinkingContent", "blocks",
                "messageStatus", "createTime",
            }
            assert assistant["thinkingContent"] == "思考过程"
            assert assistant["messageStatus"] == "NORMAL"
            assert assistant["blocks"][0]["text"] == "回答正文"

    def test_rename(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            self._seed(client.app.state.container)
            resp = client.put(
                "/agent/v1/conversations/c1/title", json={"title": "改名"},
                headers={"X-User-Id": "u1"},
            )
            assert resp.json()["code"] == "0"
            rows = client.get(
                "/agent/v1/conversations", headers={"X-User-Id": "u1"}
            ).json()["data"]
            assert rows[0]["title"] == "改名"

    def test_rename_blank_title_rejected(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            self._seed(client.app.state.container)
            body = client.put(
                "/agent/v1/conversations/c1/title", json={"title": "  "},
                headers={"X-User-Id": "u1"},
            ).json()
            assert body["code"] != "0"
            assert body["message"] == "会话标题不能为空"

    def test_rename_missing_conversation_rejected(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            body = client.put(
                "/agent/v1/conversations/c404/title", json={"title": "改名"},
                headers={"X-User-Id": "u1"},
            ).json()
            assert body["code"] != "0"
            assert body["message"] == "会话不存在"

    def test_delete_releases_from_list(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            self._seed(client.app.state.container)
            resp = client.delete("/agent/v1/conversations/c1", headers={"X-User-Id": "u1"})
            assert resp.json()["code"] == "0"
            rows = client.get(
                "/agent/v1/conversations", headers={"X-User-Id": "u1"}
            ).json()["data"]
            assert rows == []

    def test_batch_delete(self, monkeypatch):
        with TestClient(_app(monkeypatch)) as client:
            svc = client.app.state.container.agent_engine_conversation_service
            svc.touch_conversation("c1", "u1", "一")
            svc.touch_conversation("c2", "u1", "二")
            resp = client.post(
                "/agent/v1/conversations/batch-delete", json={"ids": ["c1", "c2"]},
                headers={"X-User-Id": "u1"},
            )
            assert resp.json()["code"] == "0"
            rows = client.get(
                "/agent/v1/conversations", headers={"X-User-Id": "u1"}
            ).json()["data"]
            assert rows == []
