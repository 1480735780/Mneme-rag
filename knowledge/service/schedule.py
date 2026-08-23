# -*- coding: utf-8 -*-
"""
knowledge.service.schedule - 调度登记服务（对应 Java KnowledgeDocumentScheduleServiceImpl）

文档调度字段（schedule_enabled/schedule_cron）↔ 调度表行的同步：
    - upsert_schedule：允许新建（start_chunk 事务体登记 / 上传 URL 文档）；
    - sync_schedule_if_exists：仅更新既有行（文档 enable 切换等）；
    - delete_by_doc_id：删文档时清理调度行 + 执行记录。

调度启用规则（对齐 Java syncSchedule）：仅 URL 文档可调度；cron 非空 + 文档 enabled + 调度 enabled
三条件才启用；启用时校验 cron 间隔 ≥ min_interval_seconds 并计算 next_run_time（非法抛 ClientException）。

对应 ragent 源码：
    - knowledge/service/impl/KnowledgeDocumentScheduleServiceImpl（upsertSchedule/syncScheduleIfExists/deleteByDocId）
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional

from common.exception.business import ClientException
from knowledge.enums import SourceType
from knowledge.schedule.cron_helper import CronScheduleHelper
from rag.dao.support import now_iso

logger = logging.getLogger(__name__)


class KnowledgeDocumentScheduleService:
    """调度登记服务（注入 schedule_dao + exec_dao，无状态）"""

    def __init__(self, schedule_dao, exec_dao, min_interval_seconds: int = 60):
        self._schedule = schedule_dao
        self._exec = exec_dao
        self._min_interval_seconds = min_interval_seconds

    def upsert_schedule(self, document: Optional[Dict]) -> None:
        """允许新建的调度同步（对齐 Java upsertSchedule）"""
        self._sync_schedule(document, allow_create=True)

    def sync_schedule_if_exists(self, document: Optional[Dict]) -> None:
        """仅更新既有行的调度同步（对齐 Java syncScheduleIfExists）"""
        self._sync_schedule(document, allow_create=False)

    def delete_by_doc_id(self, doc_id: Optional[str]) -> None:
        """删除文档时清理调度（对齐 Java deleteByDocId：先删执行记录再删调度行）"""
        if not doc_id or not doc_id.strip():
            return
        self._schedule.delete_by_doc(doc_id)

    # ===================== 私有 =====================

    def _sync_schedule(self, document: Optional[Dict], allow_create: bool) -> None:
        if document is None:
            return
        doc_id = document.get("id")
        kb_id = document.get("kb_id")
        if not doc_id or not kb_id:
            return
        if not SourceType.URL.value == (document.get("source_type") or "").lower():
            return

        doc_enabled = document.get("enabled") is None or document.get("enabled") == 1
        cron = document.get("schedule_cron")
        enabled = document.get("schedule_enabled") == 1
        if not (cron and cron.strip()):
            enabled = False
        if not doc_enabled:
            enabled = False

        next_run_time = None
        if enabled:
            try:
                if CronScheduleHelper.is_interval_less_than(cron, datetime.now(), self._min_interval_seconds):
                    raise ClientException(f"定时周期不能小于 {self._min_interval_seconds} 秒")
                next_run = CronScheduleHelper.next_run_time(cron, datetime.now())
                if next_run is None:
                    raise ClientException("定时表达式不合法")
                next_run_time = next_run.isoformat()
            except ValueError as exc:  # croniter 内部异常（继承 ValueError 时）
                raise ClientException("定时表达式不合法") from exc

        existing = self._schedule.get_by_doc(doc_id)
        if existing is None:
            if not allow_create:
                return
            self._schedule.insert({
                "doc_id": doc_id,
                "kb_id": kb_id,
                "cron_expr": cron.strip() if cron else None,
                "enabled": 1 if enabled else 0,
                "next_run_time": next_run_time,
            })
        else:
            self._schedule.update_by_id(existing["id"], {
                "cron_expr": cron.strip() if cron else None,
                "enabled": 1 if enabled else 0,
                "next_run_time": next_run_time,
            })
