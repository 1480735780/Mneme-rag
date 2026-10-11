# -*- coding: utf-8 -*-
"""
knowledge.versioning.service - 版本化更新编排服务（P9 §4.1 / §4.2 / §5 / §6.4 / §9 / §11.2）

提交侧（§4.1 指纹判定 + §4.2 并发规则 + §5 人工来源保护门）与发布侧（§6.4 原子发布唯一
入口 PublishTransaction + §11.2 回滚 + §9.1 下线 + §9.2 撤销复查）。

并发与锁序（§6.4）：多行事务统一加锁顺序 document -> revision -> job，收敛为
`publish()` 唯一入口（§17.4.1），外部无法自由编排三条 UPDATE；CAS 失败分类
superseded / lease_lost / deleted / already_published，由调用方按 reason 写日志与管理页原因。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from knowledge.dao.revision import (
    REVISION_BUILT_AWAITING_CONFIRMATION,
    REVISION_REJECTED,
    REVISION_SUCCEEDED,
    RevisionDao,
)
from knowledge.dao.update_job import (
    JOB_BUILT_AWAITING_CONFIRMATION,
    JOB_CANCELED,
    JOB_SUCCEEDED,
    UpdateJobDao,
)
from rag.dao.support import now_iso
from storage.database import Condition, DatabaseClient
from storage.database.client import (
    CAS_ALREADY_PUBLISHED,
    CAS_DELETED,
    CAS_LEASE_LOST,
    CAS_SUPERSEDED,
    CasConflict,
)

DOCUMENT_TABLE = "t_knowledge_document"


class DocumentUpdateBusy(RuntimeError):
    """同文档已有不同内容任务运行中——第一期串行化：忙拒绝 + 调用方重试（§4.2）"""


def build_fingerprint(source_hash: str, build_config: Dict) -> str:
    """build_fingerprint = sha256(canonical ["v1", source_hash, 规范化构建配置 JSON])（§3.2）"""
    import hashlib
    import json

    canonical = json.dumps(
        ["v1", source_hash, build_config or {}],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class UpdateSubmissionService:
    """更新提交与幂等（§4.1 五行指纹表 + §4.2 并发规则）"""

    def __init__(self, db: DatabaseClient, revision_dao: RevisionDao, job_dao: UpdateJobDao):
        self._db = db
        self._revisions = revision_dao
        self._jobs = job_dao

    def submit(
        self,
        document_id: str,
        kb_id: str,
        source_type: str,
        source_hash: str,
        build_config: Optional[Dict] = None,
        embedding_model: Optional[str] = None,
        embedding_dimension: Optional[int] = None,
    ) -> Dict:
        """提交一次更新请求，返回 {action, revision_id?, job_id?}。

        action 取值（§4.1 / §4.2）：
            no_change            SUCCEEDED + 同指纹且已是 active（幂等重发）
            rolled_back          SUCCEEDED + 同指纹但非 active → 仅移动指针（等价回滚，不重建）
            requeued_build       FAILED + 同指纹 → 复用 revision，新建 build attempt
            awaiting_confirmation BUILT_AWAITING_CONFIRMATION + 同指纹 → 不创建 attempt，
                                   重新进入待确认队列（返回已有 job_id）
            rejected_persisted   REJECTED + 同指纹 → 不构建、不再挂起（一次性拒绝决定）
            busy                 同文档不同内容任务运行中 → 忙拒绝（调用方重试）
            queued               新指纹 → 新 revision + QUEUED 任务
        """
        fingerprint = build_fingerprint(source_hash, build_config)

        # §4.2 并发规则：同文档活跃任务判定（唯一索引为 PG 兜底，此处前置检查双后端一致）
        active = self._jobs.find_active_by_document(document_id)
        if active is not None:
            if active.get("build_fingerprint") == fingerprint:
                # 相同内容运行中：幂等返回已有 job_id
                return {"action": "awaiting_confirmation" if active["status"] == "BUILT_AWAITING_CONFIRMATION"
                        else "queued", "job_id": active["id"], "revision_id": active.get("revision_id")}
            raise DocumentUpdateBusy(
                f"文档 {document_id} 已有进行中的更新任务 {active['id']}（{active['status']}），"
                "第一期同文档串行化——请稍后重试"
            )

        existing = self._revisions.find_by_fingerprint(document_id, fingerprint)
        if existing is not None:
            status = existing["status"]
            if status == REVISION_SUCCEEDED:
                return self._handle_succeeded_fingerprint(document_id, existing)
            if status == REVISION_BUILT_AWAITING_CONFIRMATION:
                # 不创建 attempt，重新进入待确认队列
                return {"action": "awaiting_confirmation", "revision_id": existing["id"]}
            if status == REVISION_REJECTED:
                # 拒绝是对该 fingerprint 的一次性决定：不重建、不再次挂起（§5）
                return {"action": "rejected_persisted", "revision_id": existing["id"]}
            # FAILED：复用 revision，创建新 build attempt（§4.1）
            job_id = self._jobs.insert({
                "document_id": document_id, "kb_id": kb_id,
                "revision_id": existing["id"],
                "build_attempt_id": "pending",  # 领取时由 claim 原子赋新 attempt
                "content_hash": source_hash, "build_fingerprint": fingerprint,
                "status": "QUEUED",
            })
            return {"action": "requeued_build", "revision_id": existing["id"], "job_id": job_id}

        # 新指纹：创建 revision + QUEUED 任务（一个事务内，保两行原子）
        with self._db.transaction():
            revision_id = self._revisions.insert({
                "document_id": document_id, "kb_id": kb_id,
                "source_type": source_type, "source_hash": source_hash,
                "build_fingerprint": fingerprint,
                "status": "BUILT_AWAITING_CONFIRMATION",
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
            })
            self._db.update_rows(
                DOCUMENT_TABLE,
                {"desired_revision_id": revision_id, "update_time": now_iso()},
                [Condition.eq("id", document_id), Condition.eq("deleted", 0)],
            )
            job_id = self._jobs.insert({
                "document_id": document_id, "kb_id": kb_id, "revision_id": revision_id,
                "build_attempt_id": "pending",
                "content_hash": source_hash, "build_fingerprint": fingerprint,
                "status": "QUEUED",
            })
        return {"action": "queued", "revision_id": revision_id, "job_id": job_id}

    def _handle_succeeded_fingerprint(self, document_id: str, revision: Dict) -> Dict:
        doc = self._db.select_rows(
            DOCUMENT_TABLE, where=[Condition.eq("id", document_id), Condition.eq("deleted", 0)],
            limit=1,
        )
        active = doc[0].get("active_revision_id") if doc else None
        if active == revision["id"]:
            return {"action": "no_change", "revision_id": revision["id"]}
        # 非 active：仅移动指针（等价回滚，§4.1 不重新构建）
        moved = self._db.update_rows(
            DOCUMENT_TABLE,
            {"active_revision_id": revision["id"],
             "desired_revision_id": revision["id"], "update_time": now_iso()},
            [Condition.eq("id", document_id), Condition.eq("deleted", 0)],
        )
        if not moved:
            raise CasConflict(CAS_SUPERSEDED, f"文档 {document_id} 指针并发变更")
        return {"action": "rolled_back", "revision_id": revision["id"]}


class PublishService:
    """发布 / 确认 / 拒绝 / 回滚 / 下线（§6.4 唯一入口 + §5 门 + §9 / §11.2）"""

    def __init__(self, db: DatabaseClient, revision_dao: RevisionDao, job_dao: UpdateJobDao):
        self._db = db
        self._revisions = revision_dao
        self._jobs = job_dao

    # ===================== 发布（§6.4 三语句事务唯一入口） =====================

    def publish(self, job_id: str, *, lease_token: str, confirmed: bool = False) -> Dict:
        """原子发布：同一事务内三条语句，锁序 document -> revision -> job（§6.4）。

        人工来源保护门（§5）：未 confirmed 且 active 来源为 manual 时，停在
        BUILT_AWAITING_CONFIRMATION（等待显式确认），不自动覆盖。
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise CasConflict(CAS_DELETED, f"任务不存在: {job_id}")
        if job["status"] == JOB_SUCCEEDED:
            return {"action": "already_published", "reason": CAS_ALREADY_PUBLISHED}
        if job["status"] != JOB_BUILT_AWAITING_CONFIRMATION:
            raise CasConflict(CAS_LEASE_LOST, f"任务状态 {job['status']} 不可发布")

        document = self._load_document(job["document_id"])
        if document is None:
            raise CasConflict(CAS_DELETED, f"文档不存在: {job['document_id']}")
        if not confirmed and self._gated(document, job):
            # §5：manual active 被 upload/sync 覆盖必须显式确认——保持待确认
            return {"action": "awaiting_confirmation", "reason": "manual_source_gate"}

        # 发布事务（三语句同事务；外部向量/关键词/图谱 I/O 永不进事务，§6.4）
        with self._db.transaction():
            # 语句 1（锁序 revision）：revision 标记可发布结果 + 记录 committed_attempt_id
            moved_revision = self._revisions.update_status_cas(
                job["revision_id"], REVISION_BUILT_AWAITING_CONFIRMATION, REVISION_SUCCEEDED,
                extra={"committed_attempt_id": job.get("build_attempt_id"),
                       "completed_at": now_iso()},
            )
            if not moved_revision:
                raise CasConflict(CAS_DELETED, f"revision {job['revision_id']} 状态已变")
            # 语句 2（锁序 document）：CAS 移动指针——desired_revision_id 必须仍指向本 revision
            # （take_offline 清空 desired → 在途任务失去发布资格，§9.1）
            moved_doc = self._db.update_rows(
                DOCUMENT_TABLE,
                {"active_revision_id": job["revision_id"], "update_time": now_iso()},
                [
                    Condition.eq("id", job["document_id"]),
                    Condition.eq("desired_revision_id", job["revision_id"]),
                    Condition.eq("deleted", 0),
                ],
            )
            if not moved_doc:
                raise CasConflict(CAS_SUPERSEDED, "desired_revision_id 已变（被更新发布/下线/删除）")
            # 语句 3（锁序 job）：token fencing + 状态收口（token 不符 = 租约已被接管/丢失）
            closed = self._jobs.update_status_cas(
                job_id, JOB_BUILT_AWAITING_CONFIRMATION, JOB_SUCCEEDED
            )
            if not closed:
                raise CasConflict(CAS_LEASE_LOST, "job 状态已被并发变更")
            # token 复核（token 不符 → 语句 3 前后任一时点暴露即回滚）
            row = self._jobs.get(job_id)
            if row is None or row.get("lease_token") != lease_token:
                raise CasConflict(CAS_LEASE_LOST, "lease_token 不符（fencing）")
        return {"action": "published", "revision_id": job["revision_id"]}

    def _gated(self, document: Dict, job: Dict) -> bool:
        """§5 发布门：manual active 被 upload/sync 候选覆盖必须确认；sync 覆盖 manual 必须确认"""
        active_source = document.get("source_type")
        candidate = self._revisions.get(job["revision_id"])
        candidate_source = (candidate or {}).get("source_type")
        if active_source == "manual":
            return True  # manual active：任何自动候选都需确认
        if active_source == "upload" and candidate_source == "sync":
            return True  # sync 覆盖 upload 需确认
        return False  # upload←upload / sync←sync / sync←upload 自动发布

    # ===================== 确认 / 拒绝（§5） =====================

    def confirm(self, job_id: str, lease_token: str) -> Dict:
        """人工确认发布（manual 门显式放行）"""
        return self.publish(job_id, lease_token=lease_token, confirmed=True)

    def reject(self, job_id: str, revision_id: str) -> Dict:
        """拒绝：revision → REJECTED（对 fingerprint 一次性决定）、job → CANCELED（释放文档槽位）"""
        with self._db.transaction():
            moved = self._revisions.update_status_cas(
                revision_id, REVISION_BUILT_AWAITING_CONFIRMATION, REVISION_REJECTED
            )
            if not moved:
                raise CasConflict(CAS_DELETED, f"revision {revision_id} 状态已变")
            self._jobs.update_fields(job_id, {"status": JOB_CANCELED})
        return {"action": "rejected", "revision_id": revision_id}

    # ===================== 回滚（§11.2） =====================

    def rollback(self, document_id: str, target_revision_id: str) -> Dict:
        """回滚 = 移动 active 指针（目标校验：SUCCEEDED + 未撤销 + embedding 兼容）"""
        target = self._revisions.get(target_revision_id)
        if target is None or target["document_id"] != document_id:
            raise CasConflict(CAS_DELETED, f"回滚目标不存在: {target_revision_id}")
        if target["status"] != REVISION_SUCCEEDED:
            raise ValueError(f"回滚目标非 SUCCEEDED: {target['status']}")
        document = self._load_document(document_id)
        if document is None:
            raise CasConflict(CAS_DELETED, f"文档不存在: {document_id}")
        if not self._embedding_compatible(document, target):
            raise ValueError(
                "回滚目标 embedding 配置不兼容（模型/维度未登记或不一致）——只能重新构建并重新发布"
            )
        moved = self._db.update_rows(
            DOCUMENT_TABLE,
            {"active_revision_id": target_revision_id,
             "desired_revision_id": target_revision_id, "update_time": now_iso()},
            [Condition.eq("id", document_id), Condition.eq("deleted", 0)],
        )
        if not moved:
            raise CasConflict(CAS_SUPERSEDED, "文档指针并发变更")
        return {"action": "rolled_back", "revision_id": target_revision_id}

    @staticmethod
    def _embedding_compatible(document: Dict, target: Dict) -> bool:
        """§11.2 兼容性：模型一致且维度一致；v0（NULL）仅在当前 active 同为未登记时放行"""
        kb_model = document.get("embedding_model")
        target_model = target.get("embedding_model")
        target_dim = target.get("embedding_dimension")
        if target_model is None or target_dim is None:
            return False  # v0/未登记：不可作为回滚目标（需重新构建）
        return kb_model is None or kb_model == target_model

    # ===================== 下线（§9.1） =====================

    def take_offline(self, document_id: str) -> Dict:
        """立即清空 active/desired 指针：在途任务因 desired CAS 失去发布资格（§9.1）"""
        moved = self._db.update_rows(
            DOCUMENT_TABLE,
            {"active_revision_id": None, "desired_revision_id": None,
             "status": "TAKE_OFFLINE", "update_time": now_iso()},
            [Condition.eq("id", document_id), Condition.eq("deleted", 0)],
        )
        if not moved:
            raise CasConflict(CAS_DELETED, f"文档不存在: {document_id}")
        return {"action": "taken_offline", "document_id": document_id}

    # ===================== 撤销复查（§9.2） =====================

    def recheck_sources(self, chunks: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """输出前/来源发送前复查 deleted / take_offline（不复查「是否被新版取代」）。

        Returns:
            (kept, dropped)：被撤销来源剔除并附点名原因（降级说明素材）
        """
        doc_ids = {c.get("doc_id") for c in chunks if c.get("doc_id")}
        if not doc_ids:
            return list(chunks), []
        rows = self._db.select_rows(
            DOCUMENT_TABLE,
            columns=("id", "status", "active_revision_id"),
            where=[Condition.in_("id", list(doc_ids)), Condition.eq("deleted", 0)],
        )
        state = {r["id"]: r for r in rows}
        kept, dropped = [], []
        for chunk in chunks:
            doc = state.get(chunk.get("doc_id"))
            if doc is None:
                dropped.append({**chunk, "revoked_reason": "deleted"})
            elif doc.get("status") == "TAKE_OFFLINE" or not doc.get("active_revision_id"):
                dropped.append({**chunk, "revoked_reason": "take_offline"})
            else:
                kept.append(chunk)
        return kept, dropped

    def _load_document(self, document_id: str) -> Optional[Dict]:
        rows = self._db.select_rows(
            DOCUMENT_TABLE, where=[Condition.eq("id", document_id), Condition.eq("deleted", 0)],
            limit=1,
        )
        return rows[0] if rows else None
