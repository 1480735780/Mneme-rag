# -*- coding: utf-8 -*-
"""
P1-1 Agent 基础框架测试：agent.config + agent.models + agent.dao
（对应 Java AgentProperties / ConditionalOnAgentEngine / dto/* / enums/* / Mapper + 服务层 DAO 用法）

覆盖：
    - 配置：EngineType 解析（默认 workflow = 决策 3B）、非法值 fail-fast、AgentProperties.from_env
      与 chat 配置 fail-fast（对齐 AgentEngineConfiguration 启动校验）
    - 模型：SSE 事件 value（meta/message/tool/hint/finish/done/cancel）、载荷 to_dict camelCase +
      None 省略（NON_NULL）、AgentBlock 形态
    - DAO：t_agent_conversation / t_agent_message（P0 表）——deleted=0 过滤（P0 登记的 dao 层
      验收项）、软删语义、last_time 排序、按 id 升序列消息（雪花时序）、轮数统计、blocks 落库
"""
import pytest

from agent.config import AgentProperties, EngineType, resolve_engine_type
from agent.dao import AGENT_CONVERSATION_TABLE, AGENT_MESSAGE_TABLE, AgentConversationDao, AgentMessageDao
from agent.models import (
    AgentBlock,
    AgentCompletionPayload,
    AgentHintPayload,
    AgentMessageDelta,
    AgentMessageStatus,
    AgentMetaPayload,
    AgentSSEEventType,
    AgentToolProgress,
)
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient


@pytest.fixture()
def db() -> InMemoryDatabaseClient:
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


# ==================== 配置 ====================


class TestEngineType:
    def test_default_agent(self, monkeypatch):
        # 决策 3B（2026-08-30）落地：默认 agent，对齐 ragent-new；退回 workflow 显式设 env
        monkeypatch.delenv("RAGENT_ENGINE_TYPE", raising=False)
        assert resolve_engine_type() is EngineType.AGENT

    def test_parse_agent(self, monkeypatch):
        monkeypatch.setenv("RAGENT_ENGINE_TYPE", "agent")
        assert resolve_engine_type() is EngineType.AGENT

    def test_invalid_fails_fast(self, monkeypatch):
        monkeypatch.setenv("RAGENT_ENGINE_TYPE", "react")
        with pytest.raises(ValueError, match="RAGENT_ENGINE_TYPE 非法"):
            resolve_engine_type()


class TestAgentProperties:
    def test_defaults(self, monkeypatch):
        for name in ("RAGENT_AGENT_PROVIDER", "RAGENT_AGENT_MODEL", "RAGENT_AGENT_MAX_ITERS",
                     "RAGENT_AGENT_MAX_RETRIES", "RAGENT_AGENT_SSE_TIMEOUT_MS"):
            monkeypatch.delenv(name, raising=False)
        props = AgentProperties.from_env()
        assert props.chat_provider == ""
        assert props.max_iters == 10
        assert props.max_retries == 2
        assert props.sse_timeout_ms == 900_000

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("RAGENT_AGENT_PROVIDER", "siliconflow")
        monkeypatch.setenv("RAGENT_AGENT_MODEL", "deepseek-ai/DeepSeek-V3")
        monkeypatch.setenv("RAGENT_AGENT_MAX_ITERS", "8")
        monkeypatch.setenv("RAGENT_AGENT_SSE_TIMEOUT_MS", "600000")
        props = AgentProperties.from_env()
        assert props.chat_provider == "siliconflow"
        assert props.chat_model == "deepseek-ai/DeepSeek-V3"
        assert props.max_iters == 8
        assert props.sse_timeout_ms == 600000

    def test_invalid_int_fails_fast(self, monkeypatch):
        monkeypatch.setenv("RAGENT_AGENT_MAX_ITERS", "abc")
        with pytest.raises(ValueError, match="RAGENT_AGENT_MAX_ITERS"):
            AgentProperties.from_env()

    def test_ensure_chat_config(self, monkeypatch):
        monkeypatch.delenv("RAGENT_AGENT_PROVIDER", raising=False)
        monkeypatch.delenv("RAGENT_AGENT_MODEL", raising=False)
        with pytest.raises(ValueError, match="agent.chat.provider"):
            AgentProperties.from_env().ensure_chat_config()
        # 齐备时静默通过
        AgentProperties(chat_provider="siliconflow", chat_model="m").ensure_chat_config()


# ==================== 模型 ====================


class TestModels:
    def test_sse_event_values(self):
        # Agent 事件协议与 workflow 两套分立；value 即 SSE event 名
        assert AgentSSEEventType.META.value == "meta"
        assert AgentSSEEventType.MESSAGE.value == "message"
        assert AgentSSEEventType.TOOL.value == "tool"
        assert AgentSSEEventType.HINT.value == "hint"
        assert AgentSSEEventType.FINISH.value == "finish"
        assert AgentSSEEventType.DONE.value == "done"
        assert AgentSSEEventType.CANCEL.value == "cancel"

    def test_message_status_values(self):
        assert AgentMessageStatus.NORMAL.value == "NORMAL"
        assert AgentMessageStatus.INTERRUPTED.value == "INTERRUPTED"

    def test_payload_camel_case_and_non_null(self):
        assert AgentMetaPayload("c1", "t1").to_dict() == {"conversationId": "c1", "taskId": "t1"}
        assert AgentMessageDelta("response", "你好").to_dict() == {"type": "response", "delta": "你好"}
        assert AgentHintPayload("MAX_ITERS", "预告").to_dict() == {"code": "MAX_ITERS", "text": "预告"}
        # None 字段省略（对应 Java @JsonInclude(NON_NULL)）
        assert AgentToolProgress(name="search_knowledge").to_dict() == {"name": "search_knowledge"}
        assert AgentToolProgress(name="t", display_name="检索", status="start", ok=True).to_dict() == {
            "name": "t", "displayName": "检索", "status": "start", "ok": True,
        }
        completion = AgentCompletionPayload(message_id="m1", message_status="INTERRUPTED").to_dict()
        assert completion == {"messageId": "m1", "messageStatus": "INTERRUPTED"}  # title 省略

    def test_agent_block_shape(self):
        tool_block = AgentBlock(kind="tool", name="search_knowledge", display_name="知识库检索",
                                status="done", result="片段…")
        d = tool_block.to_dict()
        assert d["kind"] == "tool" and d["displayName"] == "知识库检索"
        assert "text" not in d  # tool 块无 text
        answer_block = AgentBlock(kind="answer", text="结论")
        assert answer_block.to_dict() == {"kind": "answer", "text": "结论"}


# ==================== DAO ====================


class TestConversationDao:
    def test_insert_find_touch(self, db):
        dao = AgentConversationDao(db)
        cid = dao.insert("conv-1", "u1", "年假政策", last_time="2026-08-29T10:00:00")
        assert cid
        row = dao.find_active("conv-1", "u1")
        assert row is not None and row["title"] == "年假政策"
        # touch 刷新 last_time、返回不影响 title
        assert dao.touch("conv-1", "u1", last_time="2026-08-29T11:00:00") == 1
        assert dao.find_active("conv-1", "u1")["last_time"] == "2026-08-29T11:00:00"

    def test_deleted_filtered_out(self, db):
        # P0 登记的 dao 层验收项：查询只看 deleted=0（@TableLogic 语义）
        dao = AgentConversationDao(db)
        dao.insert("conv-1", "u1", "旧会话")
        assert dao.find_active("conv-1", "u1") is not None
        dao.soft_delete("conv-1", "u1")
        assert dao.find_active("conv-1", "u1") is None
        assert dao.list_by_user("u1") == []
        # 软删旧行不占用唯一键：同号可重建（ragent-new 部分唯一索引的业务等价）
        dao.insert("conv-1", "u1", "重开会话")
        assert dao.find_active("conv-1", "u1")["title"] == "重开会话"

    def test_list_by_user_orders_by_last_time_desc(self, db):
        dao = AgentConversationDao(db)
        dao.insert("c-old", "u1", "旧", last_time="2026-08-28T09:00:00")
        dao.insert("c-new", "u1", "新", last_time="2026-08-29T09:00:00")
        dao.insert("c-other", "u2", "他人", last_time="2026-08-29T10:00:00")
        rows = dao.list_by_user("u1")
        assert [r["conversation_id"] for r in rows] == ["c-new", "c-old"]

    def test_rename_does_not_touch_last_time(self, db):
        dao = AgentConversationDao(db)
        dao.insert("conv-1", "u1", "旧标题", last_time="2026-08-29T10:00:00")
        assert dao.rename("conv-1", "u1", "新标题") == 1
        row = dao.find_active("conv-1", "u1")
        assert row["title"] == "新标题"
        assert row["last_time"] == "2026-08-29T10:00:00"  # rename 不刷新 last_time（对齐 Java）


class TestMessageDao:
    def test_insert_user_and_assistant(self, db):
        conv = AgentConversationDao(db)
        msg = AgentMessageDao(db)
        conv.insert("conv-1", "u1", "标题")
        qid = msg.insert_user_message("conv-1", "u1", "年假有几天？")
        blocks = [{"kind": "answer", "text": "按规范第 3 条，年假 5 天"}]
        aid = msg.insert_assistant_message(
            "conv-1", "u1", "按规范第 3 条，年假 5 天",
            thinking_content="  ",  # 空白视同 None（对齐 blankToDefault）
            blocks=blocks,
            reply_to_message_id=qid,
        )
        rows = msg.list_by_conversation("conv-1", "u1")
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assistant = rows[1]
        assert assistant["id"] == aid and assistant["reply_to_message_id"] == qid
        assert assistant["blocks"] == blocks
        assert assistant["thinking_content"] is None
        assert assistant["message_status"] == "NORMAL"

    def test_list_orders_by_id_asc(self, db):
        conv = AgentConversationDao(db)
        msg = AgentMessageDao(db)
        conv.insert("conv-1", "u1", "标题")
        for content in ("一", "二", "三"):
            msg.insert_user_message("conv-1", "u1", content)
        rows = msg.list_by_conversation("conv-1", "u1")
        assert [r["content"] for r in rows] == ["一", "二", "三"]  # 雪花 id 升序 = 时序

    def test_mark_deleted_all_and_filter(self, db):
        conv = AgentConversationDao(db)
        msg = AgentMessageDao(db)
        conv.insert("conv-1", "u1", "标题")
        msg.insert_user_message("conv-1", "u1", "q1")
        msg.insert_assistant_message("conv-1", "u1", "a1")
        # 软删残留（对应 Java purgeResidue 的 @TableLogic 逻辑删）
        assert msg.mark_deleted_all("conv-1", "u1") == 2
        assert msg.list_by_conversation("conv-1", "u1") == []
        # 软删后轮数不计入
        assert msg.count_user_turns("u1", ["conv-1"]) == {}

    def test_count_user_turns(self, db):
        conv = AgentConversationDao(db)
        msg = AgentMessageDao(db)
        conv.insert("c1", "u1", "t1")
        conv.insert("c2", "u1", "t2")
        for _ in range(3):
            msg.insert_user_message("c1", "u1", "q")
        msg.insert_assistant_message("c1", "u1", "a")  # assistant 不计轮数
        msg.insert_user_message("c2", "u1", "q")
        msg.insert_user_message("c3", "u1", "不在列表中的会话")
        counts = msg.count_user_turns("u1", ["c1", "c2"])
        assert counts == {"c1": 3, "c2": 1}

    def test_tables_registered_in_default_tables(self):
        # P0 建表与 P1 dao 的衔接：两张表都在 DEFAULT_TABLES 里
        names = {t.name for t in DEFAULT_TABLES}
        assert AGENT_CONVERSATION_TABLE in names
        assert AGENT_MESSAGE_TABLE in names
