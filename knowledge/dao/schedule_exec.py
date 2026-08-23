# -*- coding: utf-8 -*-
"""
knowledge.dao.schedule_exec - 定时刷新执行记录数据访问（对应 Java KnowledgeDocumentScheduleExecMapper）

t_knowledge_document_schedule_exec：每次调度触发的执行记录（状态机 running/success/failed/skipped +
message + 文件快照字段 file_name/file_size/content_hash/etag/last_modified）。

对齐 Java 用法：
    - insert：RUNNING 起点（对齐 ScheduleRefreshProcessor 开头 execMapper.insert）；
    - update_result：按 id 物理更新状态/消息/结束时间/文件快照（对齐 ScheduleStateManager 各 updateById）；
    - page_by_schedule：按 schedule_id 分页（create_time desc，对齐 Mapper 通用分页）。

对应 ragent 源码：
    - knowledge/dao/mapper/KnowledgeDocumentScheduleExecMapper
    - knowledge/dao/entity/KnowledgeDocumentScheduleExecDO
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.util.snowflake import default_generator
from rag.dao.support import now_iso
from storage.database import Condition, DatabaseClient

# 执行记录表（对应 Java KnowledgeDocumentScheduleExecDO @TableName）
SCHEDULE_EXEC_TABLE = "t_knowledge_document_schedule_exec"


class KnowledgeDocumentScheduleExecDao:
    """定时执行记录数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Dict) -> str:
        """插入执行记录（RUNNING 起点）；返回主键"""
        exec_id = row.get("id") or default_generator.next_id()
        payload = dict(row)
        payload["id"] = exec_id
        now = now_iso()
        payload.setdefault("create_time", now)
        payload.setdefault("update_time", now)
        self._db.insert_row(SCHEDULE_EXEC_TABLE, payload)
        return exec_id

    def update_result(
        self,
        exec_id: str,
        status: str,
        *,
        message: Optional[str] = None,
        end_time: Optional[str] = None,
        file_name: Optional[str] = None,
        file_size: Optional[int] = None,
        content_hash: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> bool:
        """按 id 更新执行结果（对齐 Java ScheduleStateManager 各 exec updateById）；返回是否命中行"""
        updates: Dict = {"status": status, "update_time": now_iso()}
        if message is not None:
            updates["message"] = _truncate(message)
        if end_time is not None:
            updates["end_time"] = end_time
        if file_name is not None:
            updates["file_name"] = file_name
        if file_size is not None:
            updates["file_size"] = file_size
        if content_hash is not None:
            updates["content_hash"] = content_hash
        if etag is not None:
            updates["etag"] = etag
        if last_modified is not None:
            updates["last_modified"] = last_modified
        return self._db.update_rows(
            SCHEDULE_EXEC_TABLE, updates, where=[Condition.eq("id", exec_id)]
        ) > 0

    def get_by_id(self, exec_id: str) -> Optional[Dict]:
        rows = self._db.select_rows(
            SCHEDULE_EXEC_TABLE, where=[Condition.eq("id", exec_id)], limit=1
        )
        return rows[0] if rows else None

    def page_by_schedule(
        self, schedule_id: str, limit: Optional[int] = None, offset: Optional[int] = None
    ) -> Tuple[List[Dict], int]:
        """按调度分页（create_time desc）→ (rows, total)"""
        rows = self._db.select_rows(
            SCHEDULE_EXEC_TABLE,
            where=[Condition.eq("schedule_id", schedule_id)],
            order_by=[("create_time", "desc")],
        )
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total


def _truncate(value: str, max_len: int = 512) -> str:
    """对齐 Java ScheduleStateManager.truncate（512 上限）"""
    if not value:
        return value
    trimmed = value.strip()
    return trimmed if len(trimmed) <= max_len else trimmed[:max_len]
