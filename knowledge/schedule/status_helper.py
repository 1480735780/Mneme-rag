# -*- coding: utf-8 -*-
"""
knowledge.schedule.status_helper - 文档状态机辅助（对应 Java DocumentStatusHelper）

集中调度链路对文档 status 的并发控制与卡死恢复：
    - try_mark_running：CAS `status ne RUNNING → RUNNING`（附 deleted=0 + enabled=1 前置），
      显式刷新 update_time 使卡死恢复以分块开始时刻为基准（对齐 Java 注释语义）；
    - mark_failed_if_running：仅 RUNNING → FAILED（不误伤已成功/已失败）；
    - apply_refreshed_file_metadata：刷新成功后回写新文件元数据；
    - recover_stuck_running：RUNNING 超过阈值（分钟）且 enabled=1 的文档批量重置 FAILED，
      候选/实际恢复数上报（对齐 Java recoverStuckRunning：阈值下限 10 分钟）。

对应 ragent 源码：
    - knowledge/schedule/DocumentStatusHelper（tryMarkRunning/markFailedIfRunning/applyRefreshedFileMetadata/recoverStuckRunning）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Tuple

from knowledge.dao.document import KnowledgeDocumentDao
from knowledge.enums import DocumentStatus
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

logger = logging.getLogger(__name__)

# 对齐 Java：SYSTEM_USER 写入 updated_by
SYSTEM_USER = "system"

# 卡死恢复阈值下限（对齐 Java `Math.max(timeoutMinutes, 10)`）
_MIN_TIMEOUT_MINUTES = 10


@dataclass(frozen=True)
class StuckRecoveryResult:
    """卡死恢复结果（对齐 Java StuckRecoveryResult record）"""

    stuck_doc_ids: List[str]
    actual_recovered: int


class DocumentStatusHelper:
    """文档状态机辅助（注入 db + doc_dao，无状态）"""

    def __init__(self, db: DatabaseClient, doc_dao: KnowledgeDocumentDao):
        self._db = db
        self._doc_dao = doc_dao

    def try_mark_running(self, doc_id: str) -> bool:
        """领取文档运行权：仅 deleted=0 + enabled=1 + 非 RUNNING 时可置 RUNNING（对齐 Java tryMarkRunning）"""
        now = now_iso()
        return self._db.update_rows(
            "t_knowledge_document",
            {"status": DocumentStatus.RUNNING.value, "updated_by": SYSTEM_USER, "update_time": now},
            where=[
                Condition.eq("id", doc_id),
                Condition.eq("deleted", NOT_DELETED),
                Condition.eq("enabled", 1),
                Condition.ne("status", DocumentStatus.RUNNING.value),
            ],
        ) > 0

    def mark_failed_if_running(self, doc_id: str) -> None:
        """仅 RUNNING → FAILED（对齐 Java markFailedIfRunning）"""
        self._db.update_rows(
            "t_knowledge_document",
            {"status": DocumentStatus.FAILED.value, "updated_by": SYSTEM_USER},
            where=[
                Condition.eq("id", doc_id),
                Condition.eq("status", DocumentStatus.RUNNING.value),
            ],
        )

    def apply_refreshed_file_metadata(self, doc_id: str, stored) -> None:
        """刷新成功后回写新文件元数据（对齐 Java applyRefreshedFileMetadata）；更新 0 行抛「文档不存在」"""
        updated = self._db.update_rows(
            "t_knowledge_document",
            {
                "doc_name": stored.original_filename,
                "file_url": stored.url,
                "file_type": stored.detected_type,
                "file_size": stored.size,
                "updated_by": SYSTEM_USER,
            },
            where=[Condition.eq("id", doc_id)],
        )
        if updated == 0:
            from common.exception.business import ClientException

            raise ClientException("文档不存在")

    def recover_stuck_running(self, timeout_minutes: int) -> StuckRecoveryResult:
        """RUNNING 超过阈值的文档重置 FAILED（对齐 Java recoverStuckRunning）"""
        safe_timeout = max(int(timeout_minutes), _MIN_TIMEOUT_MINUTES)
        threshold = (datetime.now() - timedelta(minutes=safe_timeout)).isoformat()
        stuck_rows = self._db.select_rows(
            "t_knowledge_document",
            columns=["id"],
            where=[
                Condition.eq("status", DocumentStatus.RUNNING.value),
                Condition.eq("enabled", 1),
                Condition.lt("update_time", threshold),
            ],
        )
        stuck_ids = [r["id"] for r in stuck_rows]
        if not stuck_ids:
            return StuckRecoveryResult([], 0)
        updated = self._db.update_rows(
            "t_knowledge_document",
            {"status": DocumentStatus.FAILED.value, "updated_by": SYSTEM_USER},
            where=[
                Condition.in_("id", stuck_ids),
                Condition.eq("status", DocumentStatus.RUNNING.value),
            ],
        )
        if updated != len(stuck_ids):
            logger.warning("卡死文档恢复时部分候选状态已变化: 候选 %d 个, 实际重置 %d 个", len(stuck_ids), updated)
        return StuckRecoveryResult(stuck_ids, updated)
