# -*- coding: utf-8 -*-
"""
knowledge.dao.chunk_log - 分块执行日志数据访问（对应 Java KnowledgeDocumentChunkLogMapper）

t_knowledge_document_chunk_log：16 列（含四段/总耗时、chunk_count、error、start/end_time，无 deleted/updated_by）。
本表无软删，按 id 物理更新。

对应 ragent 源码：
    - knowledge/dao/mapper/KnowledgeDocumentChunkLogMapper
    - knowledge/dao/entity/KnowledgeDocumentChunkLogDO
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.util.snowflake import default_generator
from rag.dao.support import now_iso
from storage.database import Condition, DatabaseClient

# 分块日志表（对应 Java KnowledgeDocumentChunkLogDO @TableName）
CHUNK_LOG_TABLE = "t_knowledge_document_chunk_log"


class KnowledgeDocumentChunkLogDao:
    """分块日志数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert_running(self, doc_id: str, status: str, process_mode: str,
                       parse_profile: Optional[str] = None, pipeline_id: Optional[str] = None,
                       start_time: Optional[str] = None) -> str:
        """新建 RUNNING 分块日志（对齐 Java runChunkTask 开头：status=RUNNING + 起始时刻）"""
        log_id = default_generator.next_id()
        now = start_time or now_iso()
        self._db.insert_row(
            CHUNK_LOG_TABLE,
            {
                "id": log_id,
                "doc_id": doc_id,
                "status": status,
                "process_mode": process_mode,
                "parse_profile": parse_profile,
                "pipeline_id": pipeline_id,
                "start_time": now,
                "create_time": now,
                "update_time": now,
            },
        )
        return log_id

    def update_result(
        self,
        log_id: str,
        status: str,
        chunk_count: int,
        extract_duration: int,
        chunk_duration: int,
        embed_duration: int,
        persist_duration: int,
        total_duration: int,
        error_message: Optional[str] = None,
    ) -> bool:
        """分块收尾（对齐 Java updateChunkLog：status + 四段/总耗时 + chunk_count + error + end_time）"""
        updates: Dict = {
            "status": status,
            "chunk_count": chunk_count,
            "extract_duration": extract_duration,
            "chunk_duration": chunk_duration,
            "embed_duration": embed_duration,
            "persist_duration": persist_duration,
            "total_duration": total_duration,
            "error_message": error_message,
            "end_time": now_iso(),
            "update_time": now_iso(),
        }
        count = self._db.update_rows(CHUNK_LOG_TABLE, updates, where=[Condition.eq("id", log_id)])
        return count > 0

    def page_by_doc(self, doc_id: str, limit: Optional[int] = None, offset: Optional[int] = None) -> Tuple[List[Dict], int]:
        """该文档分块日志分页（create_time desc，对齐 Java 9.2 chunk_log create_time desc）"""
        rows = self._db.select_rows(
            CHUNK_LOG_TABLE,
            where=[Condition.eq("doc_id", doc_id)],
            order_by=[("create_time", "desc")],
        )
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total

    def delete_by_doc(self, doc_id: str) -> int:
        """删除某文档的全部分块日志（对齐 Java delete: chunkLogMapper.delete(where doc_id)）"""
        return self._db.delete_rows(CHUNK_LOG_TABLE, where=[Condition.eq("doc_id", doc_id)])