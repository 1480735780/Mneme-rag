# -*- coding: utf-8 -*-
"""
knowledge.dao.schedule - 文档定时刷新调度数据访问（对应 Java KnowledgeDocumentScheduleMapper）

t_knowledge_document_schedule：调度登记行（enabled/next_run_time/last_* 状态 + lock_owner/lock_until 行锁）。

对齐 Java 用法：
    - scan_due：对齐 ScheduleJob.scan 的 selectList 三段条件（enabled=1 ∧ (next_run_time 为空或≤now)
      ∧ (lock_until 为空或<now)），next_run_time asc + LIMIT batch；
    - try_lock：对齐 ScheduleLockManager.tryAcquire——lock_until 为空或已过期才可获取。
      单进程双后端（InMemory RLock / SQLite StaticPool 单连接）下「读行校验 + 写入」原子性有保证；
      多实例部署时 P6 换真实 PG 原子 UPDATE，本接口语义不变（偏离已登记）；
    - renew_lock / release_lock：带 lock_owner 条件（对齐 Java renew/release 的 lockOwner 等值）。

时间统一 ISO 字符串（now_iso() 约定），同格式字典序即时间序。

对应 ragent 源码：
    - knowledge/dao/mapper/KnowledgeDocumentScheduleMapper
    - knowledge/dao/entity/KnowledgeDocumentScheduleDO
    - knowledge/schedule/ScheduleLockManager（tryAcquire/renew/release）
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from common.util.snowflake import default_generator
from rag.dao.support import now_iso
from storage.database import Condition, DatabaseClient

# 调度表（对应 Java KnowledgeDocumentScheduleDO @TableName）
SCHEDULE_TABLE = "t_knowledge_document_schedule"


class KnowledgeDocumentScheduleDao:
    """调度登记数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ===================== 写 =====================

    def insert(self, row: Dict) -> str:
        """插入调度行（雪花主键 + 时间兜底）；返回主键"""
        schedule_id = row.get("id") or default_generator.next_id()
        payload = dict(row)
        payload["id"] = schedule_id
        now = now_iso()
        payload.setdefault("create_time", now)
        payload.setdefault("update_time", now)
        self._db.insert_row(SCHEDULE_TABLE, payload)
        return schedule_id

    def update_by_id(self, schedule_id: str, updates: Dict) -> bool:
        """按主键更新；返回是否命中行"""
        updates = dict(updates)
        updates.setdefault("update_time", now_iso())
        return self._db.update_rows(
            SCHEDULE_TABLE, updates, where=[Condition.eq("id", schedule_id)]
        ) > 0

    def delete_by_doc(self, doc_id: str) -> None:
        """按文档删除调度行（对齐 Java deleteByDocId 的 scheduleMapper.delete）"""
        self._db.delete_rows(SCHEDULE_TABLE, where=[Condition.eq("doc_id", doc_id)])

    # ===================== 读 =====================

    def get_by_id(self, schedule_id: str) -> Optional[Dict]:
        rows = self._db.select_rows(
            SCHEDULE_TABLE, where=[Condition.eq("id", schedule_id)], limit=1
        )
        return rows[0] if rows else None

    def get_by_doc(self, doc_id: str) -> Optional[Dict]:
        """按文档查调度行（对齐 Java selectOne eq(docId) LIMIT 1）；无则 None"""
        rows = self._db.select_rows(
            SCHEDULE_TABLE, where=[Condition.eq("doc_id", doc_id)], limit=1
        )
        return rows[0] if rows else None

    def scan_due(self, now: str, batch_size: int) -> List[Dict]:
        """到期调度行（对齐 Java scan 三段条件 + next_run_time asc + LIMIT batch）"""
        rows = self._db.select_rows(
            SCHEDULE_TABLE,
            where=[Condition.eq("enabled", 1)],
            order_by=[("next_run_time", "asc")],
        )
        due = [
            r
            for r in rows
            if (r.get("next_run_time") is None or r.get("next_run_time") <= now)
            and (r.get("lock_until") is None or r.get("lock_until") < now)
        ]
        return due[: max(batch_size, 1)]

    # ===================== 行锁（对齐 ScheduleLockManager） =====================

    def try_lock(self, schedule_id: str, owner: str, lock_until: str, now: str) -> bool:
        """尝试获取行锁：lock_until 为空或已过期才可获取（对齐 Java tryAcquire）"""
        row = self.get_by_id(schedule_id)
        if row is None:
            return False
        current = row.get("lock_until")
        if current is not None and current >= now:
            return False  # 未过期：锁仍被持有
        self._db.update_rows(
            SCHEDULE_TABLE,
            {"lock_owner": owner, "lock_until": lock_until},
            where=[Condition.eq("id", schedule_id)],
        )
        return True

    def renew_lock(self, schedule_id: str, owner: str, lock_until: str) -> bool:
        """续约：仅 owner 匹配时刷新 lock_until（对齐 Java renew 的 lockOwner 等值）"""
        return self._db.update_rows(
            SCHEDULE_TABLE,
            {"lock_until": lock_until},
            where=[Condition.eq("id", schedule_id), Condition.eq("lock_owner", owner)],
        ) > 0

    def release_lock(self, schedule_id: str, owner: str) -> bool:
        """释放：仅 owner 匹配时清空锁（对齐 Java release 的 lockOwner 等值）"""
        return self._db.update_rows(
            SCHEDULE_TABLE,
            {"lock_owner": None, "lock_until": None},
            where=[Condition.eq("id", schedule_id), Condition.eq("lock_owner", owner)],
        ) > 0

    def update_if_owned(self, schedule_id: str, owner: str, updates: Dict) -> bool:
        """仅 owner 匹配时更新（对齐 ScheduleStateManager.updateScheduleIfOwned 的 lockOwner 等值）"""
        if not updates:
            return False
        updates = dict(updates)
        updates.setdefault("update_time", now_iso())
        return self._db.update_rows(
            SCHEDULE_TABLE,
            updates,
            where=[Condition.eq("id", schedule_id), Condition.eq("lock_owner", owner)],
        ) > 0
