# -*- coding: utf-8 -*-
"""
knowledge.dao.update_job - 版本化更新任务数据访问（P9 §3.3 / §7）

面向 DatabaseClient 抽象（InMemory / Sql 双后端），行 dict 进出、软删过滤、雪花主键。

租约 fencing 的时间语义（§17.4.2，分派）：
    - Sql 后端：服务端 NOW() 表达式（`lease_until = NOW() + INTERVAL` / `lease_until < NOW()`），
      禁止客户端时间参数——多实例下时钟漂移会击穿 fencing；
    - InMemory 后端：单进程无时钟漂移，客户端 now_iso() + ISO 字典序比较（InMemory 的
      比较归一对不可数值化的 ISO 串按原生序）。

同文档串行化兜底（§17.4.5）：PG 侧部分唯一索引 uq_update_job_active_doc 捕获并发插入，
应用层把唯一冲突翻译为「返回已有 job_id」；InMemory 由 find_active_by_document 的
调用方前置检查承担（单进程无并发插入窗口）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.dao.mapper.KnowledgeUpdateJobMapper（P9 新增域）
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso, not_deleted
from storage.database import Condition, DatabaseClient, Row
from storage.database.postgres import SqlDatabaseClient

UPDATE_JOB_TABLE = "t_knowledge_update_job"

# job 状态机（§3.3）
JOB_QUEUED = "QUEUED"
JOB_RUNNING = "RUNNING"
JOB_BUILT_AWAITING_CONFIRMATION = "BUILT_AWAITING_CONFIRMATION"
JOB_SUCCEEDED = "SUCCEEDED"
JOB_FAILED_RETRYABLE = "FAILED_RETRYABLE"
JOB_FAILED_MANUAL = "FAILED_MANUAL"
JOB_CANCELED = "CANCELED"

# 活跃状态集合（同文档串行化的占用集合；与部分唯一索引的 WHERE 严格一致）
ACTIVE_JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING, JOB_BUILT_AWAITING_CONFIRMATION)


class UpdateJobDao:
    """版本化更新任务数据访问（注入 DatabaseClient；租约方法按后端分派时间语义）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ===================== 写（CRUD，双后端） =====================

    def insert(self, row: Dict) -> str:
        """插入任务（调用方填业务字段；兜底 id/lease_token/时间字段缺省与软删标记）。

        next_retry_at / lease_until 缺省置为当前时间 = 「立即可领取」（QUEUED 不受
        租约约束，但 find_claimable 的统一条件需要两字段为过去时刻）。
        """
        row = dict(row)
        row.setdefault("id", str(default_generator.next_id()))
        row.setdefault("lease_token", uuid.uuid4().hex)
        row.setdefault("next_retry_at", now_iso())
        row.setdefault("lease_until", now_iso())
        row.setdefault("deleted", NOT_DELETED)
        self._db.insert_row(UPDATE_JOB_TABLE, row)
        return row["id"]

    def update_fields(self, job_id: str, values: Dict[str, Any]) -> int:
        """通用字段更新（状态机跃迁的非竞争路径；竞争路径一律走 CAS 方法）"""
        values = dict(values)
        values["update_time"] = now_iso()
        return self._db.update_rows(
            UPDATE_JOB_TABLE, values, [Condition.eq("id", job_id), not_deleted()]
        )

    def update_status_cas(self, job_id: str, from_status: str, to_status: str) -> int:
        """CAS 状态跃迁（期望 from_status；返回 0 行由调用方按 fencing 语义处理）"""
        return self._db.update_rows(
            UPDATE_JOB_TABLE,
            {"status": to_status, "update_time": now_iso()},
            [Condition.eq("id", job_id), Condition.eq("status", from_status), not_deleted()],
        )

    # ===================== 读 =====================

    def get(self, job_id: str) -> Optional[Row]:
        rows = self._db.select_rows(
            UPDATE_JOB_TABLE, where=[Condition.eq("id", job_id), not_deleted()], limit=1
        )
        return rows[0] if rows else None

    def find_active_by_document(self, document_id: str) -> Optional[Row]:
        """同文档活跃任务（§4.2 串行化判定 + 幂等提交返回已有 job_id）"""
        rows = self._db.select_rows(
            UPDATE_JOB_TABLE,
            where=[
                Condition.eq("document_id", document_id),
                Condition.in_("status", list(ACTIVE_JOB_STATUSES)),
                not_deleted(),
            ],
            order_by=[("create_time", "desc")],
            limit=1,
        )
        return rows[0] if rows else None

    def find_claimable(self, now: str, limit: int = 10) -> List[Row]:
        """可领取任务：QUEUED 到期、FAILED_RETRYABLE 退避到期，或 RUNNING 但租约已过期
        （服务重启恢复，§4.2）。退避中的 FAILED_RETRYABLE 由 next_retry_at 门控。"""
        return self._db.select_rows(
            UPDATE_JOB_TABLE,
            where=[
                Condition.in_("status", [JOB_QUEUED, JOB_RUNNING, JOB_FAILED_RETRYABLE]),
                not_deleted(),
                Condition.lt("next_retry_at", now),
                Condition.lt("lease_until", now),
            ],
            order_by=[("next_retry_at", "asc")],
            limit=limit,
        )

    # ===================== 租约（fencing；后端分派时间语义，§17.4.2） =====================

    def claim(
        self, job_id: str, owner: str, lease_seconds: int,
        build_attempt_id: Optional[str] = None,
    ) -> Optional[Row]:
        """领取任务：QUEUED（或租约过期的 RUNNING）→ RUNNING 并授予租约。

        build_attempt_id 与租约原子同置（重试递增/重新生成，§3.3）——worker 领取即获得
        本 attempt 的 fencing 身份，过期 worker 恢复后只能留下不可见孤儿 attempt（§6.2）。

        Returns:
            Optional[Row]: 领取成功返回最新行（含 lease_token / build_attempt_id）；
            未命中（被他者领取/状态已变）返回 None——调用方放弃该任务。
        """
        if isinstance(self._db, SqlDatabaseClient):
            # 服务端时间 fencing（§17.4.2）：续期用 NOW() + INTERVAL，判定用 lease_until < NOW()
            claimed = self._db.executor.update(
                f"UPDATE {UPDATE_JOB_TABLE} SET status = ?, lease_owner = ?, "
                f"lease_token = ?, lease_until = NOW() + make_interval(secs => ?), "
                f"build_attempt_id = COALESCE(?, build_attempt_id), update_time = NOW() "
                f"WHERE id = ? AND deleted = 0 "
                f"AND (status = ? OR (status = ? AND lease_until < NOW()))",
                [JOB_RUNNING, owner, uuid.uuid4().hex, lease_seconds,
                 build_attempt_id, job_id, JOB_QUEUED, JOB_RUNNING],
            )
            if not claimed:
                return None
            return self.get(job_id)
        # InMemory：单进程，客户端时钟即全局时钟；两段条件更新各自在客户端锁内原子——
        # 未过期 RUNNING 的租约不可被抢（fencing 不因后端降级而失效）
        now = now_iso()
        token = uuid.uuid4().hex
        lease = _later_iso(now, lease_seconds)
        base_values: Dict[str, Any] = {
            "status": JOB_RUNNING, "lease_owner": owner, "lease_token": token,
            "lease_until": lease, "update_time": now,
        }
        if build_attempt_id:
            base_values["build_attempt_id"] = build_attempt_id
        claimed = self._db.update_rows(
            UPDATE_JOB_TABLE,
            dict(base_values),
            [Condition.eq("id", job_id), Condition.eq("status", JOB_QUEUED), not_deleted()],
        )
        if not claimed:
            reclaim_values = {k: v for k, v in base_values.items() if k != "status"}
            claimed = self._db.update_rows(
                UPDATE_JOB_TABLE,
                reclaim_values,
                [
                    Condition.eq("id", job_id),
                    Condition.eq("status", JOB_RUNNING),
                    Condition.lt("lease_until", now),  # 仅接管已过期租约
                    not_deleted(),
                ],
            )
        if not claimed:
            return None
        return self.get(job_id)

    def renew_lease(self, job_id: str, lease_token: str, lease_seconds: int) -> int:
        """心跳续约：token + 未过期租约双条件（fencing）。

        Returns:
            int: 命中 1 = 续约成功（满额重置）；0 = fencing 失败（心跳方必须立即停止：
            不清理、不发布，§7）。
        """
        if isinstance(self._db, SqlDatabaseClient):
            return self._db.executor.update(
                f"UPDATE {UPDATE_JOB_TABLE} "
                f"SET lease_until = NOW() + make_interval(secs => ?), update_time = NOW() "
                f"WHERE id = ? AND lease_token = ? AND lease_until >= NOW() AND deleted = 0",
                [lease_seconds, job_id, lease_token],
            )
        now = now_iso()
        return self._db.update_rows(
            UPDATE_JOB_TABLE,
            {"lease_until": _later_iso(now, lease_seconds), "update_time": now},
            [
                Condition.eq("id", job_id),
                Condition.eq("lease_token", lease_token),
                Condition.gt("lease_until", now),
                not_deleted(),
            ],
        )

    def verify_lease(self, job_id: str, lease_token: str) -> bool:
        """租约仍有效（token 匹配且未过期）——worker 关键动作（发布/清理）前的 fencing 复查"""
        row = self.get(job_id)
        if not row:
            return False
        if row.get("lease_token") != lease_token:
            return False
        if isinstance(self._db, SqlDatabaseClient):
            valid = self._db.executor.query_for_value(
                f"SELECT lease_until >= NOW() FROM {UPDATE_JOB_TABLE} "
                f"WHERE id = ? AND deleted = 0",
                [job_id],
            )
            return bool(valid)
        return (row.get("lease_until") or "") >= now_iso()


def _later_iso(now: str, seconds: int) -> str:
    """ISO 串 + seconds（InMemory 租约专用；ISO 同格式字典序比较成立）"""
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(now)
    return (base + timedelta(seconds=seconds)).isoformat()
