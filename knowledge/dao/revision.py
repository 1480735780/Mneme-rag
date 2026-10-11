# -*- coding: utf-8 -*-
"""
knowledge.dao.revision - 不可变版本数据访问（P9 §3.2，对应 Java 侧 RevisionDO Mapper 语义）

面向 DatabaseClient 抽象（InMemory / Sql 双后端无感知），行 dict 进出、软删过滤、雪花主键。
revision 行发布后不可修改——本 dao 只提供状态机跃迁（含 CAS 形态，供发布事务在
db.transaction() 内调用），不提供任意字段更新。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.dao.mapper.KnowledgeRevisionMapper（P9 新增域）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso, not_deleted
from storage.database import Condition, DatabaseClient, Row

REVISION_TABLE = "t_knowledge_revision"

# revision 状态机（§3.2；发布可见性由 Document.active_revision_id 指针表达，无 PUBLISHED 态）
REVISION_BUILT_AWAITING_CONFIRMATION = "BUILT_AWAITING_CONFIRMATION"
REVISION_SUCCEEDED = "SUCCEEDED"
REVISION_FAILED = "FAILED"
REVISION_REJECTED = "REJECTED"
REVISION_REVOKED = "REVOKED"


class RevisionDao:
    """不可变版本数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ===================== 写 =====================

    def insert(self, row: Dict) -> str:
        """插入 revision（调用方填业务字段；本 dao 兜底 id/status 之外的缺省与软删标记）"""
        row = dict(row)
        row.setdefault("id", str(default_generator.next_id()))
        row.setdefault("deleted", NOT_DELETED)
        row.setdefault("created_at", now_iso())
        self._db.insert_row(REVISION_TABLE, row)
        return row["id"]

    def update_status(self, revision_id: str, status: str, completed_at: Optional[str] = None) -> int:
        """无条件状态跃迁（重试/失败/拒绝等非竞争路径）"""
        values: Dict[str, Any] = {"status": status, "update_time": now_iso()}
        if completed_at:
            values["completed_at"] = completed_at
        return self._db.update_rows(
            REVISION_TABLE, values, [Condition.eq("id", revision_id), not_deleted()]
        )

    def update_status_cas(
        self,
        revision_id: str,
        from_status: str,
        to_status: str,
        extra: Optional[Dict] = None,
    ) -> int:
        """CAS 状态跃迁：期望 from_status 才跃迁（发布事务在 db.transaction() 内调用，
        返回 0 行由调用方 raise CasConflict 触发整体回滚，§6.4）"""
        values: Dict[str, Any] = {"status": to_status, "update_time": now_iso()}
        values.update(extra or {})
        return self._db.update_rows(
            REVISION_TABLE,
            values,
            [Condition.eq("id", revision_id), Condition.eq("status", from_status), not_deleted()],
        )

    # ===================== 读 =====================

    def get(self, revision_id: str) -> Optional[Row]:
        rows = self._db.select_rows(
            REVISION_TABLE, where=[Condition.eq("id", revision_id), not_deleted()], limit=1
        )
        return rows[0] if rows else None

    def find_by_fingerprint(self, document_id: str, build_fingerprint: str) -> Optional[Row]:
        """指纹判定（§4.1）：同文档同 fingerprint 的 revision（幂等提交/回滚判定入口）。
        同 fingerprint 理论唯一；多行（历史边界）时取最新创建的一条。"""
        rows = self._db.select_rows(
            REVISION_TABLE,
            where=[
                Condition.eq("document_id", document_id),
                Condition.eq("build_fingerprint", build_fingerprint),
                not_deleted(),
            ],
            order_by=[("created_at", "desc")],
            limit=1,
        )
        return rows[0] if rows else None

    def list_by_document(self, document_id: str, statuses: Optional[List[str]] = None) -> List[Row]:
        """文档的 revision 列表（回滚候选枚举 §11.2）；statuses 为空 = 全部"""
        conditions = [Condition.eq("document_id", document_id), not_deleted()]
        if statuses:
            conditions.append(Condition.in_("status", statuses))
        return self._db.select_rows(
            REVISION_TABLE,
            where=conditions,
            order_by=[("created_at", "desc")],
        )
