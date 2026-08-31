# -*- coding: utf-8 -*-
"""
P1 Agent MVP：Agent 控制器测试（POST /agent/chat）

覆盖：
    - 正常：200 + Result 包装 + camelCase 结构（answer/steps/iterations/error）
    - history 原样透传
    - 空 question → 400

假容器注入假 agent_service，跑真实路由（对齐 eval 控制器测试先例）。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag.controller.agent_controller import router


class _FakeAgentService:
    def __init__(self):
        self.last = None

    async def chat(self, question, history=None):
        self.last = (question, history)
        return {
            "answer": "北京明天晴 25°C",
            "iterations": 2,
            "error": None,
            "steps": [
                {"tool": "weather_query", "params": {"city": "北京"}, "observation": "晴 25°C", "ok": True}
            ],
        }


def _client(service):
    app = FastAPI()
    app.state.container = type("C", (), {"agent_service": service})()
    app.include_router(router)
    return TestClient(app)


class TestAgentChat:
    def test_normal_response_camelcase(self):
        service = _FakeAgentService()
        client = _client(service)
        resp = client.post("/agent/chat", json={"question": "北京天气？"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["answer"] == "北京明天晴 25°C"
        assert data["iterations"] == 2
        assert data["error"] is None
        assert len(data["steps"]) == 1
        step = data["steps"][0]
        assert step["tool"] == "weather_query"
        assert step["params"] == {"city": "北京"}
        assert step["observation"] == "晴 25°C"
        assert step["ok"] is True
        assert service.last == ("北京天气？", None)

    def test_history_passthrough(self):
        service = _FakeAgentService()
        client = _client(service)
        history = [{"role": "user", "content": "之前的问题"}, {"role": "assistant", "content": "之前的回答"}]
        resp = client.post("/agent/chat", json={"question": "继续", "history": history})
        assert resp.status_code == 200
        assert service.last[1] == history

    def test_empty_question_returns_400(self):
        client = _client(_FakeAgentService())
        resp = client.post("/agent/chat", json={"question": "   "})
        assert resp.status_code == 400

    def test_missing_question_returns_400(self):
        client = _client(_FakeAgentService())
        resp = client.post("/agent/chat", json={})
        assert resp.status_code == 400

    def test_invalid_history_structure_returns_422(self):
        # P0-3：history 由 Pydantic 结构化校验——缺 content 的非法项 → 422
        client = _client(_FakeAgentService())
        resp = client.post("/agent/chat", json={"question": "继续", "history": [{"role": "user"}]})
        assert resp.status_code == 422
        resp2 = client.post("/agent/chat", json={"question": "继续", "history": [{"role": "user", "content": "ok"}, "不是对象"]})
        assert resp2.status_code == 422
