# -*- coding: utf-8 -*-
"""scripts.seed 幂等初始化测试：admin 播种 / 内置档案激活 / 8 槽位 / 重复执行不重复"""
import pytest

from scripts.seed import (
    PROMPT_DEFAULTS,
    ensure_admin,
    ensure_builtin_profile,
    ensure_prompts,
    run,
)
from rag.dao.agent_dao import BUILTIN_TRUE, AgentPromptDao, AgentProfileDao
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient
from user.dao.user_dao import UserDao
from user.service.password import verify_password

EXPECTED_SLOTS = {
    "SYSTEM_CHAT",
    "MCP_ANSWER",
    "MIXED_ANSWER",
    "KB_ANSWER",
    "CONVERSATION_SUMMARY",
    "RECOMMENDED_QUESTIONS",
    "AGENT_MAIN",
    "KNOWLEDGE_TOOL_DESCRIPTION",
}


@pytest.fixture()
def db() -> InMemoryDatabaseClient:
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


def test_seed_creates_admin_with_hash(db):
    summary = run(db)
    admin = UserDao(db).find_by_username("admin")
    assert admin is not None
    assert admin["id"] == summary["admin_id"]
    assert admin["role"] == "admin"
    # 密码为 PBKDF2 哈希，不落明文，且可校验
    assert admin["password"].startswith("pbkdf2$")
    assert verify_password("admin123", admin["password"])
    assert not verify_password("wrong", admin["password"])


def test_seed_creates_active_builtin_profile(db):
    run(db)
    profile_dao = AgentProfileDao(db)
    active = profile_dao.find_active()
    assert active is not None
    assert active["builtin"] == BUILTIN_TRUE
    assert active["name"] == "内置助手"


def test_seed_inserts_eight_prompt_slots(db):
    run(db)
    profile_id = ensure_builtin_profile(db)
    slots = AgentPromptDao(db).list_by_agent(profile_id)
    assert {s["slot_key"] for s in slots} == EXPECTED_SLOTS
    # 必需占位符保留在默认内容中
    summary = next(s for s in slots if s["slot_key"] == "CONVERSATION_SUMMARY")
    assert "{summary_max_chars}" in summary["content"]
    rec = next(s for s in slots if s["slot_key"] == "RECOMMENDED_QUESTIONS")
    for ph in ("{chunks}", "{count}", "{question}", "{answer}"):
        assert ph in rec["content"]
    # v1.1 Agent 槽位内容完整（移植自 ragent-new 260812_agent_engine.sql，防截断）
    agent_main = next(s for s in slots if s["slot_key"] == "AGENT_MAIN")
    for section in ("# 身份", "# 工具选择", "# 调用方式", "# 结果处理"):
        assert section in agent_main["content"]
    tool_desc = next(s for s in slots if s["slot_key"] == "KNOWLEDGE_TOOL_DESCRIPTION")
    assert "参数 query" in tool_desc["content"]


def test_seed_is_idempotent(db):
    first = run(db)
    second = run(db)
    assert first["admin_id"] == second["admin_id"]
    assert first["profile_id"] == second["profile_id"]
    assert second["inserted_slot_count"] == 0
    # 用户 / 档案 / 槽位数量不随重复执行增长
    assert UserDao(db).count() == 1
    assert len(AgentProfileDao(db).list()) == 1
    assert len(AgentPromptDao(db).list_by_agent(first["profile_id"])) == len(PROMPT_DEFAULTS)


def test_seed_does_not_overwrite_existing_slot(db):
    # 预置自定义槽位内容
    dao = AgentPromptDao(db)
    pid = ensure_builtin_profile(db)
    dao.save(pid, "SYSTEM_CHAT", "自定义系统提示词（不应被覆盖）")
    inserted = ensure_prompts(db, pid)
    assert "SYSTEM_CHAT" not in inserted
    row = dao.find_by_agent_slot(pid, "SYSTEM_CHAT")
    assert row["content"] == "自定义系统提示词（不应被覆盖）"


def test_ensure_admin_respects_env_override(db, monkeypatch):
    monkeypatch.setenv("RAGENT_INIT_ADMIN_USERNAME", "root")
    monkeypatch.setenv("RAGENT_INIT_ADMIN_PASSWORD", "secret-123")
    uid = ensure_admin(db)
    admin = UserDao(db).find_by_username("root")
    assert admin is not None
    assert admin["id"] == uid
    assert verify_password("secret-123", admin["password"])
