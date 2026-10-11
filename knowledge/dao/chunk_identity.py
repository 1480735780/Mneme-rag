# -*- coding: utf-8 -*-
"""
knowledge.dao.chunk_identity - chunk 自然键注册表数据访问（P9 §3.4）

注册表是父子回填、引用解析、计数对账和精确清理的硬依赖（不是诊断附件）。写入语义：
    - 完整自然键入表，(document_id, revision_id, build_attempt_id, chunk_index) 四元组唯一；
    - 截断 hash 碰撞（不同自然键 → 同 chunk_key_id）必须失败：PG 由主键/唯一索引捕获，
      InMemory 由本 dao 显式查重捕获——两侧语义一致，发现碰撞一律抛 ChunkIdentityConflict。
"""
from __future__ import annotations

from typing import List, Optional

from rag.dao.support import NOT_DELETED, now_iso, not_deleted
from storage.database import Condition, DatabaseClient, Row

CHUNK_IDENTITY_TABLE = "t_knowledge_chunk_identity"


class ChunkIdentityConflict(RuntimeError):
    """chunk 自然键/截断主键冲突（§3.4：发现碰撞必须失败，不静默覆盖）"""


class ChunkIdentityDao:
    """chunk 自然键注册表数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Row) -> str:
        """写入注册行；四元组或 chunk_key_id 冲突 → ChunkIdentityConflict（必失败，不覆盖）"""
        row = dict(row)
        row.setdefault("deleted", NOT_DELETED)
        row.setdefault("create_time", now_iso())
        # InMemory 无唯一约束，显式查重（PG 由主键 + 唯一索引捕获，此处前置检查两侧语义一致）
        key_id = row["chunk_key_id"]
        if self.get(key_id) is not None:
            raise ChunkIdentityConflict(
                f"chunk_key_id 冲突: {key_id}（截断 hash 碰撞或重复写入，禁止静默覆盖）"
            )
        dup = self._db.select_rows(
            CHUNK_IDENTITY_TABLE,
            where=[
                Condition.eq("document_id", row["document_id"]),
                Condition.eq("revision_id", row["revision_id"]),
                Condition.eq("build_attempt_id", row["build_attempt_id"]),
                Condition.eq("chunk_index", row["chunk_index"]),
                not_deleted(),
            ],
            limit=1,
        )
        if dup:
            raise ChunkIdentityConflict(
                f"自然键四元组冲突: ({row['document_id']}, {row['revision_id']}, "
                f"{row['build_attempt_id']}, {row['chunk_index']}) 已存在"
            )
        self._db.insert_row(CHUNK_IDENTITY_TABLE, row)
        return key_id

    def insert_batch(self, rows: List[Row]) -> int:
        """批量写入；任一冲突整体失败（调用方决定放弃 attempt 或换号重试）"""
        for row in rows:
            self.insert(row)
        return len(rows)

    def get(self, chunk_key_id: str) -> Optional[Row]:
        rows = self._db.select_rows(
            CHUNK_IDENTITY_TABLE,
            where=[Condition.eq("chunk_key_id", chunk_key_id), not_deleted()],
            limit=1,
        )
        return rows[0] if rows else None

    def get_by_chunk_id(self, chunk_id: str) -> Optional[Row]:
        """引用解析：物理 chunk_id → 注册行（来源归因/引用回填用）"""
        rows = self._db.select_rows(
            CHUNK_IDENTITY_TABLE,
            where=[Condition.eq("chunk_id", chunk_id), not_deleted()],
            limit=1,
        )
        return rows[0] if rows else None

    def list_by_attempt(
        self,
        document_id: str,
        revision_id: str,
        build_attempt_id: str,
        chunk_role: Optional[str] = None,
    ) -> List[Row]:
        """attempt 域注册行（计数对账 §6.3 / 精确清理 §11.3 的目标枚举）"""
        conditions = [
            Condition.eq("document_id", document_id),
            Condition.eq("revision_id", revision_id),
            Condition.eq("build_attempt_id", build_attempt_id),
            not_deleted(),
        ]
        if chunk_role:
            conditions.append(Condition.eq("chunk_role", chunk_role))
        return self._db.select_rows(
            CHUNK_IDENTITY_TABLE,
            where=conditions,
            order_by=[("chunk_index", "asc")],
        )

    def count_by_attempt(
        self,
        document_id: str,
        revision_id: str,
        build_attempt_id: str,
        chunk_role: Optional[str] = None,
    ) -> int:
        """attempt 域计数（完整性门槛：与 expected_child_count / expected_parent_count 对账）"""
        return len(self.list_by_attempt(document_id, revision_id, build_attempt_id, chunk_role))

    def delete_by_attempt(
        self, document_id: str, revision_id: str, build_attempt_id: str
    ) -> int:
        """attempt 域精确清理（§11.3：worker 只清理自己租约下的 attempt；软删标记）"""
        return self._db.update_rows(
            CHUNK_IDENTITY_TABLE,
            {"deleted": 1, "update_time": now_iso()},
            [
                Condition.eq("document_id", document_id),
                Condition.eq("revision_id", revision_id),
                Condition.eq("build_attempt_id", build_attempt_id),
                not_deleted(),
            ],
        )

    def delete_by_attempt_id(self, build_attempt_id: str) -> int:
        """按 attempt id 清理（attempt 全局唯一，purge_attempt 通道用；软删标记）"""
        return self._db.update_rows(
            CHUNK_IDENTITY_TABLE,
            {"deleted": 1, "update_time": now_iso()},
            [Condition.eq("build_attempt_id", build_attempt_id), not_deleted()],
        )
