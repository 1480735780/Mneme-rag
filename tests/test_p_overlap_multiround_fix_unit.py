# -*- coding: utf-8 -*-
"""
V2.1.1 overlap bug 修复回归测试

Bug 现场（真 LLM benchmark C1/C2 暴露）：
    _do_compress 里 `if after_id >= history_start_id: return` 在短会话里
    永远命中，导致修正型对话的 round 2 内容永远进不了摘要。

修复：把 overlap 检查从"history_start_id"改成"cutoff_id 是否被 after_id 覆盖"，
只要窗口里出现新 user 消息把 cutoff 推后就继续压。

本测试确保：
    1. Round 1（少量消息）无新内容 → 不写
    2. Round 2 有新内容 → 写入包含 round 1
    3. Round 3 又新增用户消息把 cutoff 推后 → 再次触发压缩
    4. Round 3 摘要内容包含 round 2 引入的"修正"关键词（旧 bug 下会丢）
    5. Round 4 无新消息（cutoff 未推后）→ 不重复压缩（防止回归到过度压缩）
"""
from __future__ import annotations

import json
from typing import List

import pytest

from core.llm.enums import Tier
from core.llm.schema import ChatRequest
from rag.memory.config import MemoryProperties
from rag.memory.summary import DatabaseConversationMemorySummaryService
from rag.prompt.builder import AgentPromptSlot, StaticAgentPromptResolver
from storage.database import InMemoryDatabaseClient


class _StubLLM:
    def __init__(self, outputs: List[str]):
        self._outputs = list(outputs)
        self.call_count = 0

    async def chat(self, request: ChatRequest, tier: Tier = Tier.FAST) -> str:
        self.call_count += 1
        if not self._outputs:
            raise AssertionError("LLM stub 无输出")
        return self._outputs.pop(0)


def _make_service(llm: _StubLLM) -> DatabaseConversationMemorySummaryService:
    from rag.prompt.builder import DEFAULT_AGENT_PROMPTS

    db = InMemoryDatabaseClient(
        {"t_message": [], "t_conversation_summary": [], "t_conversation": []}
    )
    service = DatabaseConversationMemorySummaryService(
        db=db,
        llm_service=llm,  # type: ignore[arg-type]
        prompt_resolver=StaticAgentPromptResolver(
            prompts={
                AgentPromptSlot.CONVERSATION_SUMMARY.name:
                    DEFAULT_AGENT_PROMPTS[AgentPromptSlot.CONVERSATION_SUMMARY.name]
            }
        ),
        properties=MemoryProperties(
            summary_enabled=True,
            summary_start_turns=1,
            history_keep_turns=4,
            summary_max_chars=500,
        ),
    )
    return service


def _seed(db, seq: int, role: str, content: str) -> int:
    db.insert_row(
        "t_message",
        {
            "id": f"{seq:05d}",
            "conversation_id": "c1",
            "user_id": "u1",
            "role": role,
            "content": content,
            "sources": [],
            "deleted": 0,
        },
    )
    return seq + 1


def _state_json(goal: str = "g", critical: List[str] = None) -> str:
    return json.dumps(
        {
            "user_goal": goal,
            "progress": [],
            "open_questions": [],
            "critical_context": critical or [],
        },
        ensure_ascii=False,
    )


# ==================== 主 bug 复现 ====================


def test_multi_round_correction_survives_compression():
    """
    多轮修正场景（模拟 benchmark C1）：
      R1: user 引入 DB_ENGINE=MYSQL_X
      R2: user 修正为 DB_ENGINE=POSTGRESQL_Y
      R3: user 追加干扰话题，把 cutoff 推后
    V2 服务必须在 R3 那次压缩时把 R2 的修正也纳入摘要。
    修复前 R3 会被 overlap 检查错误跳过，摘要停在只有 R1 内容的状态。
    """
    llm = _StubLLM(outputs=[])
    service = _make_service(llm)
    db = service._db

    # 手工挂 outputs 让每次压缩返回带 critical 的 state
    llm._outputs = [
        _state_json("选型讨论", ["DB_ENGINE=MYSQL_X"]),   # 第 1 次压缩产出
        _state_json("选型讨论修正", ["DB_ENGINE=POSTGRESQL_Y"]),  # 第 2 次压缩产出
    ]

    seq = 1
    # Round 1
    seq = _seed(db, seq, "user", "开始选型：DB_ENGINE=MYSQL_X")
    seq = _seed(db, seq, "assistant", "记录")

    # Round 1 只 1 用户，cutoff=user1，to_summarize 空，不写
    service._do_compress("c1", "u1")
    assert service._find_latest_summary("c1", "u1") is None

    # Round 2: 修正
    seq = _seed(db, seq, "user", "改成 DB_ENGINE=POSTGRESQL_Y")
    seq = _seed(db, seq, "assistant", "OK")

    # Round 2: latest_user_turns=[user2, user1], cutoff=latest[0]=user2 (id 00003)
    # after_id=None (无摘要), to_summarize=(00001,00002) → 写入含 round 1
    service._do_compress("c1", "u1")
    r2 = service._find_latest_summary("c1", "u1")
    assert r2 is not None
    r2_structured = r2["structured_content"]
    if isinstance(r2_structured, str):
        r2_structured = json.loads(r2_structured)
    # 第 1 次压缩 LLM 拿到 round 1 消息，输出 MYSQL_X
    assert "MYSQL_X" in json.dumps(r2_structured, ensure_ascii=False)

    # Round 3: 干扰
    seq = _seed(db, seq, "user", "聊聊日志格式")
    seq = _seed(db, seq, "assistant", "json")
    seq = _seed(db, seq, "user", "监控端口")
    seq = _seed(db, seq, "assistant", "9090")

    # Round 3: latest_user_turns=[user4,user3,user2,user1], cutoff=latest[1]=user3 (00005)
    # after_id=asst1 (00002), cutoff (00005) > after_id → **旧逻辑用 history_start_id
    # (user1=00001) 判定, after_id >= history_start_id 会 return; 修复后走 cutoff 判定,
    # cutoff > after_id 继续压, to_summarize=(00003, 00004)=user2+asst2 (POSTGRESQL_Y 内容)**
    service._do_compress("c1", "u1")
    r3 = service._find_latest_summary("c1", "u1")
    assert r3 is not None
    assert r3["id"] != r2["id"], "R3 必须写入新行"
    r3_structured = r3["structured_content"]
    if isinstance(r3_structured, str):
        r3_structured = json.loads(r3_structured)
    # 第 2 次压缩 LLM 输出 POSTGRESQL_Y（模拟它确实读了 user2 的修正内容）
    assert "POSTGRESQL_Y" in json.dumps(r3_structured, ensure_ascii=False), \
        "C1/C2 bug 未修好: round 3 摘要没含 round 2 引入的修正"


def test_cutoff_not_advanced_still_skips():
    """
    回归反向保护：如果 cutoff 没被推后（无新 user 消息把窗口滑动），
    overlap 检查仍应跳过避免过度压缩浪费 LLM 调用。
    """
    llm = _StubLLM(outputs=[])
    llm._outputs = [_state_json("g1", ["fact A"]), _state_json("g2", ["fact B"])]
    service = _make_service(llm)
    db = service._db

    # 4 条 user + 4 条 asst 触发压缩，然后不再新增
    seq = 1
    for i in range(4):
        seq = _seed(db, seq, "user", f"问 {i}")
        seq = _seed(db, seq, "assistant", f"答 {i}")

    # 第 1 次
    service._do_compress("c1", "u1")
    calls_after_first = llm.call_count
    assert calls_after_first >= 1

    # 第 2 次: 无新消息，cutoff 位置未变，overlap 应跳过
    service._do_compress("c1", "u1")
    calls_after_second = llm.call_count
    assert calls_after_second == calls_after_first, \
        "无新消息时不应再次调 LLM（会浪费调用量）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
