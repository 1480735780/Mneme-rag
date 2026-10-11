# -*- coding: utf-8 -*-
"""
knowledge.versioning.sweeper - 独立清理器（P9 §11.3 / §17.5.1）

边界（§11.3，冻结）：
    - worker 只清理自己的 attempt；文档/revision 级清理交给本 sweeper；
    - 删除目标精确到 (document_id, revision_id, build_attempt_id)，**禁止**调用
      遗留 delete_document_vectors(doc_id)；
    - 事务内确认目标非 active、未撤销、宽限期已过；
    - 先 dry-run：只输出「将删除 X 行」对账日志不真删，运行一周确认无误判后再开启删除
      （§17.5.1）；宽限期不拍脑袋——默认占位 30 分钟，上线前用流式请求 p99 实测校准。

保留策略（§11.1）：active 永留；最近一个 SUCCEEDED（回滚候选）长留；其余被替代
revision 超宽限期后清理（软删 revision + 注册表，物理向量/关键词行经 purge_attempt 精确清）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from knowledge.dao.chunk_identity import ChunkIdentityDao
from knowledge.dao.revision import REVISION_SUCCEEDED, RevisionDao
from storage.database import Condition, DatabaseClient

DOCUMENT_TABLE = "t_knowledge_document"


class VersioningSweeper:
    """被替代 revision 的保留期清理（dry-run 优先；双后端）"""

    def __init__(
        self,
        db: DatabaseClient,
        revision_dao: RevisionDao,
        identity_dao: ChunkIdentityDao,
        writer=None,
        *,
        grace_seconds: int = 1800,
    ):
        """
        Args:
            writer: ChunkIndexWriter（attempt 域精确清理通道）；None = 只做 dry-run 对账
            grace_seconds: 查询宽限期（§11.1；默认占位 30 分钟，p99 实测后校准）
        """
        self._db = db
        self._revisions = revision_dao
        self._identity = identity_dao
        self._writer = writer
        self._grace_seconds = grace_seconds

    def find_purgeable(self, now: str) -> List[Dict]:
        """枚举清理目标：非 active、非回滚候选（最近一个 SUCCEEDED）、超宽限期的 SUCCEEDED revision"""
        cutoff = (datetime.fromisoformat(now) - timedelta(seconds=self._grace_seconds)).isoformat()
        targets: List[Dict] = []
        doc_rows = self._db.select_rows(
            DOCUMENT_TABLE,
            columns=("id", "active_revision_id"),
            where=[Condition.eq("deleted", 0)],
        )
        for doc in doc_rows:
            doc_id = doc["id"]
            revisions = self._revisions.list_by_document(doc_id)
            succeeded = [r for r in revisions if r["status"] == REVISION_SUCCEEDED]
            # 保留集：active + 回滚候选（非 active 中最近一个 SUCCEEDED，§11.1 长留、不受宽限期约束）
            keep = {doc.get("active_revision_id")}
            candidates = [r for r in succeeded if r["id"] not in keep]
            if candidates:
                rollback_candidate = max(
                    candidates, key=lambda r: (r.get("completed_at") or r.get("created_at") or "")
                )
                keep.add(rollback_candidate["id"])
            for rev in revisions:
                if rev["id"] in keep or rev.get("deleted") == 1:
                    continue
                completed = rev.get("completed_at") or rev.get("update_time") or ""
                if not completed or completed > cutoff:
                    continue  # 宽限期未过（覆盖最长流式请求/回填/引用解析）
                targets.append({
                    "document_id": doc_id,
                    "revision_id": rev["id"],
                    "build_attempt_id": rev.get("committed_attempt_id") or "",
                    "status": rev["status"],
                    "completed_at": completed,
                })
        return targets

    def run(self, now: str, dry_run: bool = True) -> Dict:
        """执行清理：dry_run=True 只输出「将删除 X 行」对账日志（§17.5.1 上线节奏）"""
        targets = self.find_purgeable(now)
        report: Dict = {"dry_run": dry_run, "targets": len(targets), "items": []}
        for t in targets:
            registry_rows = self._identity.count_by_attempt(
                t["document_id"], t["revision_id"], t["build_attempt_id"]
            ) if t["build_attempt_id"] else 0
            item = {**t, "registry_rows": registry_rows}
            report["items"].append(item)
            if dry_run:
                continue
            # 事务内确认非 active（§11.3），然后三通道精确清理 + revision 软删
            with self._db.transaction():
                doc = self._db.select_rows(
                    DOCUMENT_TABLE, columns=("active_revision_id",),
                    where=[Condition.eq("id", t["document_id"]), Condition.eq("deleted", 0)],
                    limit=1,
                )
                if not doc or doc[0].get("active_revision_id") == t["revision_id"]:
                    report["skipped_active"] = report.get("skipped_active", 0) + 1
                    continue
                if self._writer is not None and t["build_attempt_id"]:
                    import asyncio

                    asyncio.run(self._writer.purge_attempt(
                        self._collection_of(t["document_id"]), t["build_attempt_id"]
                    ))
                self._identity.delete_by_attempt_id(t["build_attempt_id"]) if t["build_attempt_id"] else None
                self._db.update_rows(
                    "t_knowledge_revision",
                    {"deleted": 1, "update_time": now},
                    [Condition.eq("id", t["revision_id"])],
                )
        return report

    def _collection_of(self, document_id: str) -> str:
        """文档 → collection（经 t_knowledge_base.collection_name）"""
        rows = self._db.select_rows(
            "t_knowledge_base",
            columns=("collection_name",),
            where=[Condition.eq(
                "id",
                (self._db.select_rows(
                    DOCUMENT_TABLE, columns=("kb_id",),
                    where=[Condition.eq("id", document_id)], limit=1,
                )[0].get("kb_id") or ""),
            )],
            limit=1,
        )
        return rows[0]["collection_name"] if rows else ""
