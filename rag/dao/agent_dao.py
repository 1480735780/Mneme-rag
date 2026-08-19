# -*- coding: utf-8 -*-
"""
rag.dao.agent_dao - 智能体档案与提示词槽位数据访问（对应 Java AgentProfileMapper + AgentPromptMapper）

面向 DatabaseClient 抽象编程，表 t_agent_profile / t_agent_prompt。服务「Agent 档案管理 CRUD +
全局单激活 + 提示词槽位管理写路径」。

对齐 Java AgentProfileAdminServiceImpl / AgentPromptMapper 语义：
    - ProfileDao.activate：**单事务（本方法内串行两步）「先清全部 active=1 → 置目标 active=1」**，
      对齐 Java activate 的「显式 set 清零 + updateById 置位」；返回被激活记录，目标不存在返回 None
    - PromptDao.save：按 agent_id + slot_key 先查后写 upsert（对齐 AgentPromptMapper upsert INSERT_UPDATE）

边界（§4.4）：t_agent_prompt 的**读路径**（resolve/loadOwnPrompts）复用既有 DatabaseAgentPromptResolver
（rag/prompt/agent_resolver.py），本模块仅新增管理端写路径，不重复实现读取。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.entity.AgentProfileDO / AgentPromptDO
    - com.nageoffer.ai.ragent.rag.dao.mapper.AgentProfileMapper / AgentPromptMapper
"""

from __future__ import annotations

from typing import Dict, List, Optional

from common.context.user_context import UserContext
from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, fill_audit, mark_deleted, now_iso
from storage.database import Condition, DatabaseClient, Row

# Agent 表（对应 Java DO @TableName）
AGENT_PROFILE_TABLE = "t_agent_profile"
AGENT_PROMPT_TABLE = "t_agent_prompt"

# 内置标记 / 激活标记（对齐 Java AgentProfileDO：builtin=1 内置 / active=1 激活，全局仅一条 active=1）
BUILTIN_TRUE = 1
BUILTIN_FALSE = 0
ACTIVE_TRUE = 1
ACTIVE_FALSE = 0


class AgentProfileDao:
    """智能体档案数据访问（注入 DatabaseClient）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def create(
        self,
        *,
        name: str,
        description: Optional[str] = None,
        avatar: Optional[str] = None,
        builtin: int = BUILTIN_FALSE,
    ) -> str:
        """
        创建档案（主动 active=0，对齐 Java create：builtin=0 / active=0 + 审计落库）

        Returns:
            档案主键 ID（雪花生成）
        """
        row: Row = {
            "id": default_generator.next_id(),
            "name": name,
            "description": description,
            "avatar": avatar,
            "builtin": builtin,
            "active": ACTIVE_FALSE,
            "deleted": NOT_DELETED,
        }
        fill_audit(row)
        self._db.insert_row(AGENT_PROFILE_TABLE, row)
        return row["id"]

    def find_by_id(self, pid: str) -> Optional[Dict]:
        """按主键查档案（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            AGENT_PROFILE_TABLE,
            where=[
                Condition.eq("id", pid),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def find_active(self) -> Optional[Dict]:
        """查询当前激活档案（active=1 且未删）；无激活返回 None"""
        rows = self._db.select_rows(
            AGENT_PROFILE_TABLE,
            where=[
                Condition.eq("active", ACTIVE_TRUE),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def list(self) -> List[Dict]:
        """列出全部档案（软删过滤，无排序要求）"""
        return self._db.select_rows(
            AGENT_PROFILE_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
        )

    def update(
        self,
        pid: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> bool:
        """
        按主键部分更新（仅传非空字段 + 刷新 update_by/update_time，对齐 Java updateById 逐字段 set）

        Returns:
            bool: 是否存在命中档案（软删过滤）
        """
        values: Row = {"update_by": UserContext.get_user_id(), "update_time": now_iso()}
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if avatar is not None:
            values["avatar"] = avatar
        count = self._db.update_rows(
            AGENT_PROFILE_TABLE,
            values,
            where=[
                Condition.eq("id", pid),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def delete(self, pid: str) -> bool:
        """软删档案（deleted=1 + 审计，对齐 Java deleteById 逻辑删除）"""
        count = self._db.update_rows(
            AGENT_PROFILE_TABLE,
            mark_deleted(),
            where=[
                Condition.eq("id", pid),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def activate(self, pid: str) -> Optional[Dict]:
        """
        激活档案，对齐 Java activate（mustLoad 先行校验）：

            0) 先校验目标存在（软删过滤）；不存在立即返回 None，**不执行任何修改**（失败路径无副作用，
               避免把当前激活档案一并清零）；
            1) 将全部 active=1 的记录清零（保持全局仅一条 active=1）；
            2) 置目标 active=1 并刷新 update 审计。

        注意：两步为**串行、非跨语句原子事务**（DatabaseClient 无事务 API）。进程内
        InMemory 实现经 RLock 偶发原子；真实 SQL 后端在并发下存在短暂中间态（对齐 Java 语义，
        一致性收敛于最终结果）。

        Returns:
            被激活的档案记录；目标不存在（软删过滤）返回 None
        """
        # 步骤 0：先校验目标存在（对齐 Java mustLoad），不存在则不做任何修改
        target = self.find_by_id(pid)
        if target is None:
            return None
        # 步骤 1：全局清零 active（对齐 Java update set active=0 where active=1，不限定 deleted）
        self._db.update_rows(
            AGENT_PROFILE_TABLE,
            {"active": ACTIVE_FALSE},
            where=[Condition.eq("active", ACTIVE_TRUE)],
        )
        # 步骤 2：置位目标
        self._db.update_rows(
            AGENT_PROFILE_TABLE,
            {"active": ACTIVE_TRUE, "update_by": UserContext.get_user_id(), "update_time": now_iso()},
            where=[
                Condition.eq("id", pid),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        # 返回激活后态快照（active=1 + 刷新后审计），而非激活前 target
        return self.find_by_id(pid)


class AgentPromptDao:
    """智能体提示词槽位数据访问（管理端写路径；读路径复用 DatabaseAgentPromptResolver）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def save(self, agent_id: str, slot_key: str, content: Optional[str]) -> str:
        """
        保存槽位提示词（按 agent_id + slot_key 先查后写 upsert，对齐 AgentPromptMapper upsert）

        已有记录：更新 content（含空白覆盖）与 update 审计；无记录：新建（含插槽 key 与审计）。

        Returns:
            提示词记录主键 ID
        """
        existing = self.find_by_agent_slot(agent_id, slot_key)
        now = now_iso()
        if existing is not None:
            self._db.update_rows(
                AGENT_PROMPT_TABLE,
                {"content": content, "update_by": UserContext.get_user_id(), "update_time": now},
                where=[
                    Condition.eq("agent_id", agent_id),
                    Condition.eq("slot_key", slot_key),
                    Condition.eq("deleted", NOT_DELETED),
                ],
            )
            return existing["id"]
        pid = default_generator.next_id()
        row: Row = {
            "id": pid,
            "agent_id": agent_id,
            "slot_key": slot_key,
            "content": content,
            "deleted": NOT_DELETED,
        }
        fill_audit(row)
        self._db.insert_row(AGENT_PROMPT_TABLE, row)
        return pid

    def find_by_agent_slot(self, agent_id: str, slot_key: str) -> Optional[Dict]:
        """按 agent_id + slot_key 查槽位（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            AGENT_PROMPT_TABLE,
            where=[
                Condition.eq("agent_id", agent_id),
                Condition.eq("slot_key", slot_key),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def list_by_agent(self, agent_id: str) -> List[Dict]:
        """按 agent 列全部槽位（软删过滤）"""
        return self._db.select_rows(
            AGENT_PROMPT_TABLE,
            where=[
                Condition.eq("agent_id", agent_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )