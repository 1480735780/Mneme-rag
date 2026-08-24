# -*- coding: utf-8 -*-
"""
rag.service.agent_service - Agent 聊天门面（POST /agent/chat 依赖）

薄门面：持有 AgentPipeline，把 AgentResult 转成 snake_case dict（controller 边界 camelize）。
history 入参 [{role, content}] → [Message]（非法行丢弃；空列表返回 None 由管线兜底）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class AgentChatService:
    """Agent 对话门面（对比文档 §12 P1 Agent MVP 的对外入口）"""

    def __init__(self, pipeline: Any):
        self._pipeline = pipeline

    async def chat(
        self,
        question: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """执行 Agent 闭环 → snake_case dict（steps 逐项含 tool/params/observation/ok）"""
        history_messages = self._to_messages(history)
        result = await self._pipeline.run(question, history_messages)
        return {
            "answer": result.answer,
            "iterations": result.iterations,
            "error": result.error,
            "steps": [
                {"tool": s.tool, "params": s.params, "observation": s.observation, "ok": s.ok}
                for s in result.steps
            ],
        }

    @staticmethod
    def _to_messages(history: Optional[List[Dict[str, Any]]]) -> Optional[List[Any]]:
        """[{role, content}] → [Message]；空/非法行丢弃；整体为空返回 None"""
        if not history:
            return None
        from core.llm.schema import Message

        messages = []
        for item in history:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if not role or not content:
                continue
            if role == "user":
                messages.append(Message.user(content))
            elif role == "assistant":
                messages.append(Message.assistant(content))
        return messages or None
