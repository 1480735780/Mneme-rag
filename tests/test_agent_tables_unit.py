# -*- coding: utf-8 -*-
"""
v1.1 Agent 执行架构域表结构测试：t_agent_conversation / t_agent_message / t_agent_state
（对齐 ragent-new v2.0.0 260812_agent_engine.sql / AgentConversationDO / AgentMessageDO）

覆盖：
    - 3 张表注册进 DEFAULT_TABLES，列名对齐 Java DO 字段
    - t_agent_message：blocks JSONB 轨迹列；无 workflow 专属列（sources / retrieved_chunks 等）
    - t_agent_state：三列复合主键、payload JSONB
    - Sql DDL：复合主键走表级 PRIMARY KEY 约束（内联多主键 PG 报错），单主键仍内联；DDL 幂等前缀
    - InMemory：ensure_schema 登记新表 + 时间列自动填充 / 条件查询接线
"""
from datetime import datetime

from storage.database import Condition, InMemoryDatabaseClient, SqlDatabaseClient
from storage.database.executor import RecordingSqlExecutor
from storage.database.schema import DEFAULT_TABLES

_AGENT_TABLES = ("t_agent_conversation", "t_agent_message", "t_agent_state")


def _table(name: str):
    return next(t for t in DEFAULT_TABLES if t.name == name)


# ==================== 表注册与列结构 ====================


class TestAgentTablesSchema:
    def test_all_registered(self):
        names = {t.name for t in DEFAULT_TABLES}
        for name in _AGENT_TABLES:
            assert name in names, f"{name} 未注册进 DEFAULT_TABLES"

    def test_conversation_columns(self):
        cols = _table("t_agent_conversation").column_names()
        for required in (
            "id", "conversation_id", "user_id", "title",
            "last_time", "create_time", "update_time", "deleted",
        ):
            assert required in cols, f"缺列 {required}"

    def test_message_columns(self):
        table = _table("t_agent_message")
        cols = table.column_names()
        for required in (
            "id", "conversation_id", "user_id", "role",
            "content", "thinking_content", "blocks",
            "reply_to_message_id", "message_status",
            "create_time", "update_time", "deleted",
        ):
            assert required in cols, f"缺列 {required}"
        # blocks 为运行轨迹块序列（reasoning/answer/tool），JSONB 承载
        blocks = next(c for c in table.columns if c.name == "blocks")
        assert blocks.data_type == "JSONB"
        # 终答双写无来源无角标：workflow 消息表的专属列不进 Agent 消息表
        for absent in ("sources", "retrieved_chunks", "recommended_questions", "thinking_duration"):
            assert absent not in cols, f"多列 {absent}"

    def test_state_composite_pk(self):
        table = _table("t_agent_state")
        cols = table.column_names()
        for required in ("user_id", "session_id", "state_key", "payload", "create_time", "update_time"):
            assert required in cols, f"缺列 {required}"
        payload = next(c for c in table.columns if c.name == "payload")
        assert payload.data_type == "JSONB"
        pk = [c.name for c in table.columns if c.primary_key]
        assert pk == ["user_id", "session_id", "state_key"]


# ==================== Sql DDL 构造 ====================


class TestAgentTablesDDL:
    def _ddl(self, table_name: str) -> str:
        rec = RecordingSqlExecutor()
        SqlDatabaseClient(rec).ensure_schema(DEFAULT_TABLES)
        calls = [sql for _, sql, _ in rec.calls if table_name in sql]
        assert calls, f"{table_name} 未生成 DDL"
        return calls[0]

    def test_state_uses_table_level_composite_pk(self):
        ddl = self._ddl("t_agent_state")
        assert "PRIMARY KEY (user_id, session_id, state_key)" in ddl
        # 无内联主键（一表多个内联 PRIMARY KEY 在 PG 上非法）
        assert ddl.count("PRIMARY KEY") == 1

    def test_conversation_keeps_inline_pk(self):
        ddl = self._ddl("t_agent_conversation")
        assert "id VARCHAR(32) PRIMARY KEY" in ddl
        assert ddl.count("PRIMARY KEY") == 1

    def test_message_ddl_generated(self):
        ddl = self._ddl("t_agent_message")
        assert ddl.startswith("CREATE TABLE IF NOT EXISTS t_agent_message")
        assert "blocks JSONB" in ddl

    def test_all_agent_tables_idempotent_prefix(self):
        for name in _AGENT_TABLES:
            assert self._ddl(name).startswith(f"CREATE TABLE IF NOT EXISTS {name}")


# ==================== InMemory 接线 ====================


class TestAgentTablesInMemory:
    def _db(self) -> InMemoryDatabaseClient:
        client = InMemoryDatabaseClient()
        client.ensure_schema(DEFAULT_TABLES)
        return client

    def test_state_insert_autofills_time(self):
        db = self._db()
        db.insert_row(
            "t_agent_state",
            {"user_id": "u1", "session_id": "s1", "state_key": "agent_state", "payload": {"k": "v"}},
        )
        rows = db.select_rows("t_agent_state")
        assert len(rows) == 1
        assert rows[0]["payload"] == {"k": "v"}
        # 复合主键表无 id 列，时间列仍按 ensure_schema 登记自动填充
        datetime.fromisoformat(rows[0]["create_time"])
        datetime.fromisoformat(rows[0]["update_time"])

    def test_state_select_by_composite_key(self):
        db = self._db()
        db.insert_row("t_agent_state", {"user_id": "u1", "session_id": "s1", "state_key": "agent_state"})
        db.insert_row("t_agent_state", {"user_id": "u2", "session_id": "s1", "state_key": "agent_state"})
        rows = db.select_rows(
            "t_agent_state",
            where=[
                Condition.eq("user_id", "u1"),
                Condition.eq("session_id", "s1"),
                Condition.eq("state_key", "agent_state"),
            ],
        )
        assert len(rows) == 1
        assert rows[0]["user_id"] == "u1"

    def test_message_roundtrip_with_blocks(self):
        db = self._db()
        blocks = [
            {"type": "reasoning", "content": "先检索知识库"},
            {"type": "tool", "name": "search_knowledge", "status": "success"},
            {"type": "answer", "content": "依据规范第 3 条……"},
        ]
        db.insert_row(
            "t_agent_message",
            {
                "id": "m-1",
                "conversation_id": "c-1",
                "user_id": "u1",
                "role": "assistant",
                "content": "依据规范第 3 条……",
                "blocks": blocks,
                "message_status": "NORMAL",
            },
        )
        rows = db.select_rows("t_agent_message", where=[Condition.eq("conversation_id", "c-1")])
        assert len(rows) == 1
        assert rows[0]["blocks"] == blocks
