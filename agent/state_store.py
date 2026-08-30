# -*- coding: utf-8 -*-
"""
agent.state_store - AgentScope 工作状态存储（对应 Java PgAgentStateStore）

payload 为框架自有编码的不透明 JSON（业务侧不解析）：Python 侧由 AgentState
（pydantic）经 dump_state/load_state 编解码，本存储只搬运字符串。
匿名会话的 user_id 统一归一为 __anon__（对齐 Java）。

upsert 语义以「查后写」实现（DatabaseClient 无 upsert 表达；ragent-new 的 PG
ON CONFLICT 在并发首写场景由后写整体覆盖收敛，payload 是全量替换、两写皆自洽）。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.state.PgAgentStateStore（Java 侧实现框架
    AgentStateStore 接口；agentscope Python 2.0.7 的 Agent 不注入 store，
    状态的加载/回存由 P1-4 服务层经本类完成，接口面与 Java 保持一致）
"""
from __future__ import annotations

from typing import Any, List, Optional

from rag.dao.support import now_iso
from storage.database import Condition, DatabaseClient, Row

AGENT_STATE_TABLE = "t_agent_state"

ANONYMOUS_USER = "__anon__"

# AgentState 侧固定传的状态键（对齐 Java：state_key 由 AgentScope 侧固定传入）
DEFAULT_STATE_KEY = "agent_state"


def dump_state(state: Any) -> str:
    """AgentState（pydantic）→ JSON 串（框架自有编码的编码侧）"""
    return state.model_dump_json()


def load_state(payload: Optional[str]):
    """JSON 串 → AgentState；空白/畸形返回 None（对齐 Java get 的 Optional.empty）"""
    from agentscope.state import AgentState

    if not payload or not payload.strip():
        return None
    try:
        return AgentState.model_validate_json(payload)
    except Exception:  # noqa: BLE001 畸形 payload 视同无状态（对齐 Java 异常即 empty 的容错）
        return None


class PgAgentStateStore:
    """t_agent_state 访问（注入 DatabaseClient；InMemory / PG 双后端同构）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def save(self, user_id: str, session_id: str, state: Any, key: str = DEFAULT_STATE_KEY) -> None:
        """保存/覆盖指定会话的状态（整体替换；payload 由 dump_state 编码）"""
        payload = state if isinstance(state, str) else dump_state(state)
        safe_user = self._safe_user(user_id)
        existing = self._query(safe_user, session_id, key)
        if existing is not None:
            self._db.update_rows(
                AGENT_STATE_TABLE,
                {"payload": payload, "update_time": now_iso()},
                where=[
                    Condition.eq("user_id", safe_user),
                    Condition.eq("session_id", session_id),
                    Condition.eq("state_key", key),
                ],
            )
            return
        self._db.insert_row(
            AGENT_STATE_TABLE,
            {
                "user_id": safe_user,
                "session_id": session_id,
                "state_key": key,
                "payload": payload,
            },
        )

    def get(self, user_id: str, session_id: str, key: str = DEFAULT_STATE_KEY):
        """读取状态（反序列化为 AgentState；无记录/空白/畸形 → None）"""
        payload = self._query(self._safe_user(user_id), session_id, key)
        return load_state(payload)

    def get_payload(self, user_id: str, session_id: str, key: str = DEFAULT_STATE_KEY) -> Optional[str]:
        """原始 payload 读取（不解析；供诊断与跨版本迁移）"""
        return self._query(self._safe_user(user_id), session_id, key)

    def exists(self, user_id: str, session_id: str) -> bool:
        """会话是否有任何状态记录"""
        rows = self._db.select_rows(
            AGENT_STATE_TABLE,
            columns=["session_id"],
            where=[Condition.eq("user_id", self._safe_user(user_id)), Condition.eq("session_id", session_id)],
            limit=1,
        )
        return bool(rows)

    def delete(self, user_id: str, session_id: str, key: Optional[str] = None) -> int:
        """删除状态：key 为 None 删整个会话（对应 Java delete(session) / delete(session, key)）"""
        conditions = [Condition.eq("user_id", self._safe_user(user_id)), Condition.eq("session_id", session_id)]
        if key is not None:
            conditions.append(Condition.eq("state_key", key))
        return self._db.delete_rows(AGENT_STATE_TABLE, where=conditions)

    # ==================== 内部 ====================

    @staticmethod
    def _safe_user(user_id: Optional[str]) -> str:
        """空/空白用户归一为匿名（对齐 Java safeUser）"""
        return user_id if (user_id or "").strip() else ANONYMOUS_USER

    def _query(self, safe_user: str, session_id: str, key: str) -> Optional[str]:
        rows: List[Row] = self._db.select_rows(
            AGENT_STATE_TABLE,
            columns=["payload"],
            where=[
                Condition.eq("user_id", safe_user),
                Condition.eq("session_id", session_id),
                Condition.eq("state_key", key),
            ],
        )
        if not rows:
            return None
        return rows[0].get("payload")
