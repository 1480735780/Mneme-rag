# -*- coding: utf-8 -*-
"""
scripts.seed - 幂等初始化数据脚本（对应 ragent 数据初始化播种）

播种三部分（全部幂等，可重复执行）：
    1. admin 账号      （用户名/密码 env 覆盖：RAGENT_INIT_ADMIN_USERNAME/PASSWORD，默认 admin/admin123；
                         密码 PBKDF2 哈希落库，不落明文）
    2. 内置 Agent Profile（builtin=1, active=1；已存在则跳过，无激活时补激活）
    3. 6 个内置 Prompt 槽位（SYSTEM_CHAT / MCP_ANSWER / MIXED_ANSWER / KB_ANSWER /
                          CONVERSATION_SUMMARY / RECOMMENDED_QUESTIONS；槽位已存在则不覆盖）

用法（项目根目录）：
    python -m scripts.seed            # 需已配 RAGENT_DATABASE_URL（seed 面向持久化数据库）
    RAGENT_INIT_ADMIN_USERNAME=root RAGENT_INIT_ADMIN_PASSWORD=secret python -m scripts.seed

对应 ragent 源码：
    - 数据初始化器/内置智能体播种（t_user / t_agent_profile / t_agent_prompt）
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from rag.dao.agent_dao import (
    ACTIVE_FALSE,
    ACTIVE_TRUE,
    BUILTIN_TRUE,
    AgentPromptDao,
    AgentProfileDao,
)
from rag.dao.support import NOT_DELETED
from rag.prompt.builder import DEFAULT_AGENT_PROMPTS
from storage.database import DEFAULT_TABLES, DatabaseClient
from user.dao.user_dao import UserDao
from user.enums import UserRole
from user.service.password import hash_password

# 播种配置默认值（可用环境变量覆盖）
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

# 内置智能体档案
BUILTIN_PROFILE_NAME = "内置助手"
BUILTIN_PROFILE_DESCRIPTION = "系统内置智能体：默认激活，承载各 Prompt 槽位的默认提示词。"

# 6 个内置槽位默认提示词（槽位名 → 内容；已存在的槽位不被 seed 覆盖）
# RECOMMENDED_QUESTIONS 复用 builder.DEFAULT_AGENT_PROMPTS 的代码级默认（含必需占位符）
PROMPT_DEFAULTS: Dict[str, str] = {
    "SYSTEM_CHAT": (
        "你是 mneme-rag 的智能助手。基于知识库内容回答用户问题，"
        "回答简洁、准确；引用知识库内容时注明来源，不确定时如实说明。"
    ),
    "MCP_ANSWER": (
        "你是智能助手。严格依据外部工具返回的结果组织回答，"
        "引用工具输出的事实，不编造工具未提供的信息；工具失败时如实告知。"
    ),
    "MIXED_ANSWER": (
        "你是智能助手。综合知识库片段与外部工具结果回答用户问题，"
        "区分信息来源、避免相互矛盾；无法综合时优先采用知识库事实。"
    ),
    "KB_ANSWER": (
        "你是知识库问答助手。严格基于提供的知识库片段回答；"
        "片段中不包含答案时如实说明「知识库中未找到相关信息」，不要编造。"
    ),
    "CONVERSATION_SUMMARY": (
        "你是会话摘要助手。请将以下历史对话压缩为不超过 {summary_max_chars} 字的摘要，"
        "保留关键事实、已解决的问题与尚未解决的疑问，使用中文输出。"
    ),
    "RECOMMENDED_QUESTIONS": DEFAULT_AGENT_PROMPTS["RECOMMENDED_QUESTIONS"],
}


def ensure_admin(db: DatabaseClient, username: Optional[str] = None, password: Optional[str] = None) -> str:
    """确保 admin 账号存在（幂等：同名用户已存在则跳过）。返回用户 ID。"""
    dao = UserDao(db)
    name = (username or os.environ.get("RAGENT_INIT_ADMIN_USERNAME") or DEFAULT_ADMIN_USERNAME).strip()
    pwd = password or os.environ.get("RAGENT_INIT_ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD
    existing = dao.find_by_username(name)
    if existing is not None:
        return existing["id"]
    uid = f"seed-{name}"
    dao.insert(
        {
            "id": uid,
            "username": name,
            "password": hash_password(pwd),
            "avatar": "",
            "role": UserRole.ADMIN.value,
            "deleted": NOT_DELETED,
        }
    )
    return uid


def ensure_builtin_profile(db: DatabaseClient) -> str:
    """确保内置 Agent Profile 存在且激活（幂等）。返回档案 ID。"""
    dao = AgentProfileDao(db)
    existing = next((p for p in dao.list() if p.get("builtin") == BUILTIN_TRUE), None)
    if existing is not None:
        if dao.find_active() is None:
            dao.activate(existing["id"])
        return existing["id"]
    pid = dao.create(
        name=BUILTIN_PROFILE_NAME,
        description=BUILTIN_PROFILE_DESCRIPTION,
        builtin=BUILTIN_TRUE,
    )
    dao.activate(pid)
    return pid


def ensure_prompts(db: DatabaseClient, agent_id: str) -> List[str]:
    """为内置档案插入缺失的 Prompt 槽位（已存在不覆盖）。返回本次插入的槽位名列表。"""
    dao = AgentPromptDao(db)
    inserted: List[str] = []
    for slot_key, content in PROMPT_DEFAULTS.items():
        if dao.find_by_agent_slot(agent_id, slot_key) is None:
            dao.save(agent_id, slot_key, content)
            inserted.append(slot_key)
    return inserted


def run(db: DatabaseClient) -> Dict[str, object]:
    """执行种子（幂等）：建表 → admin → 内置档案 → 6 槽位。返回本次动作摘要。"""
    db.ensure_schema(DEFAULT_TABLES)
    admin_id = ensure_admin(db)
    profile_id = ensure_builtin_profile(db)
    inserted = ensure_prompts(db, profile_id)
    return {
        "admin_id": admin_id,
        "profile_id": profile_id,
        "inserted_slots": inserted,
        "inserted_slot_count": len(inserted),
    }


def main() -> int:
    """CLI 入口：`python -m scripts.seed`（需 RAGENT_DATABASE_URL，seed 面向持久化数据库）"""
    from app.config import AppSettings
    from app.wiring import _build_database

    settings = AppSettings.from_env()
    if not (settings.database_url or "").strip():
        print(
            "未配置 RAGENT_DATABASE_URL：seed 需要持久化数据库。\n"
            "示例：RAGENT_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ragent "
            "python -m scripts.seed"
        )
        return 2
    db = _build_database(settings)
    summary = run(db)
    print(
        f"seed 完成：admin_id={summary['admin_id']}, profile_id={summary['profile_id']}, "
        f"新增槽位 {summary['inserted_slot_count']} 个: {summary['inserted_slots'] or '(无，均已存在)'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
