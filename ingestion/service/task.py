# -*- coding: utf-8 -*-
"""
ingestion.service.task - 摄取任务服务（对应 Java IngestionTaskServiceImpl）

    - execute：按 source 创建任务 → engine **同步**执行（裁定：跟随 Java，非异步 dispatcher）→
      NodeLog 逐个落 task_node → 回写任务状态
    - upload：multipart 字节入口（MIME 按文件名探测），落 FILE 源后同 execute
    - get / page（status 过滤，create_time desc）/ list_nodes（node_order asc）

对齐 Java 细节：
    - 任务 insert 即 RUNNING（PENDING 枚举保留给未来异步入口）
    - logs_json 存摘要（output 置空）；output_json 落完整节点输出但 1MB 截断
    - task_node.status：非 success → failed；message 以 "Skipped:" 前缀 → skipped

对应 ragent 源码：
    - ingestion/service/impl/IngestionTaskServiceImpl
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from common.context.user_context import UserContext
from common.exception.business import ClientException
from ingestion.dao.task import IngestionTaskDao
from ingestion.dao.task_node import IngestionTaskNodeDao
from ingestion.domain.context import DocumentSource, IngestionContext, NodeLog
from ingestion.domain.enums import IngestionStatus, SourceType
from ingestion.domain.pipeline import PipelineDefinition
from ingestion.domain.result import IngestionResult
from ingestion.engine.engine import IngestionEngine
from ingestion.service.pipeline import IngestionPipelineService
from rag.dao.support import now_iso
from rag.ingestion.kernel import MimeTypeDetector

# 输出 JSON 截断上限（对齐 Java truncateOutputJson 的 1MB）
_OUTPUT_JSON_MAX = 1024 * 1024

logger = logging.getLogger(__name__)


class IngestionTaskService:
    """摄取任务服务（对齐 Java IngestionTaskServiceImpl）"""

    def __init__(
        self,
        engine: IngestionEngine,
        pipeline_service: IngestionPipelineService,
        task_dao: IngestionTaskDao,
        task_node_dao: IngestionTaskNodeDao,
    ):
        self._engine = engine
        self._pipeline_service = pipeline_service
        self._task_dao = task_dao
        self._task_node_dao = task_node_dao

    async def execute(self, pipeline_id: str, source: DocumentSource,
                      vector_space_id=None, actor: Optional[str] = None) -> IngestionResult:
        """按文档源创建并执行任务（对齐 Java execute）"""
        if source is None:
            raise ClientException("文档来源不能为空")
        return await self._execute_internal(pipeline_id, source, None, None,
                                            vector_space_id, actor)

    async def upload(self, pipeline_id: str, content: bytes, file_name: Optional[str],
                     actor: Optional[str] = None) -> IngestionResult:
        """上传字节入口：MIME 按文件名探测（对齐 Java upload）"""
        if not content:
            raise ClientException("文件不能为空")
        file_name = (file_name or "").strip() or "upload.bin"
        mime_type = MimeTypeDetector.detect(content, file_name)
        source = DocumentSource(type=SourceType.FILE, location=file_name, file_name=file_name)
        return await self._execute_internal(pipeline_id, source, content, mime_type,
                                            None, actor)

    def get(self, task_id: str) -> Dict:
        """按 id 查任务 VO；不存在抛 ClientException"""
        task = self._task_dao.get_by_id(task_id)
        if task is None:
            raise ClientException("未找到任务")
        return _to_vo(task)

    def page(self, current: int = 1, size: int = 10,
             status: Optional[str] = None) -> Dict:
        """分页（status 精确过滤，create_time desc）→ {records,total,current,size}"""
        current = current if current and current > 0 else 1
        size = size if size and size > 0 else 10
        normalized = _normalize_status(status)
        rows, total = self._task_dao.page(size, (current - 1) * size, normalized)
        return {
            "records": [_to_vo(r) for r in rows],
            "total": total,
            "current": current,
            "size": size,
        }

    def list_nodes(self, task_id: str) -> List[Dict]:
        """按任务查节点运行记录（node_order asc，对齐 Java listNodes）"""
        return [_to_node_vo(r) for r in self._task_node_dao.list_by_task(task_id)]

    # ---- 内部 ----

    async def _execute_internal(
        self,
        pipeline_id: Optional[str],
        source: DocumentSource,
        raw_bytes: Optional[bytes],
        mime_type: Optional[str],
        vector_space_id,
        actor: Optional[str],
    ) -> IngestionResult:
        if not pipeline_id or not pipeline_id.strip():
            raise ClientException("必须传流水线ID")
        actor = actor if actor is not None else UserContext.get_username()
        pipeline = self._pipeline_service.get_definition(pipeline_id)

        now = now_iso()
        task_id = self._task_dao.insert({
            "pipeline_id": pipeline_id,
            "source_type": source.type.value if source.type is not None else None,
            "source_location": source.location,
            "source_file_name": source.file_name,
            "status": IngestionStatus.RUNNING.value,
            "chunk_count": 0,
            "started_at": now,
            "created_by": actor,
            "updated_by": actor,
        })

        context = IngestionContext(
            task_id=task_id,
            pipeline_id=pipeline_id,
            source=source,
            raw_bytes=raw_bytes,
            mime_type=mime_type,
            vector_space_id=vector_space_id,
            logs=[],
        )
        try:
            result = await self._engine.execute(pipeline, context)
        except ClientException:
            # 引擎校验异常（环/多起始/缺连线/前置校验）：对齐 Java @Transactional 回滚语义
            # ——任务记录不残留 RUNNING（Python 无跨端事务，等价物为删除刚插入的任务 + 上抛）。
            self._rollback_task(task_id)
            raise
        self._save_node_logs(task_id, pipeline_id, pipeline, result.logs)
        self._update_task_from_context(task_id, result)
        return IngestionResult(
            task_id=task_id,
            pipeline_id=pipeline_id,
            status=result.status,
            chunk_count=len(result.chunks or []),
            message="OK" if result.error is None else str(result.error),
        )

    def _rollback_task(self, task_id: str) -> None:
        """回滚刚插入的任务（对齐 Java 事务回滚）：删 task 及可能的 task_node 残留，静默幂等"""
        try:
            self._task_node_dao.delete_by_task(task_id)
        except Exception:  # noqa: BLE001 —— 回滚清理失败不阻断主流程
            logger.warning("回滚清理 task_node 失败 task_id=%s", task_id, exc_info=True)
        try:
            self._task_dao.delete_by_id(task_id)
        except Exception:  # noqa: BLE001 —— 回滚删除失败不阻断主流程
            logger.warning("回滚删除 task 失败 task_id=%s", task_id, exc_info=True)

    def _save_node_logs(self, task_id: str, pipeline_id: str,
                        pipeline: PipelineDefinition, logs: Optional[List[NodeLog]]) -> None:
        if not logs:
            return
        order_map = _build_node_order_map(pipeline)
        for log in logs:
            if log is None:
                continue
            self._task_node_dao.insert({
                "task_id": task_id,
                "pipeline_id": pipeline_id,
                "node_id": log.node_id,
                "node_type": log.node_type,
                "node_order": order_map.get(log.node_id, 0),
                "status": _resolve_node_status(log),
                "duration_ms": log.duration_ms,
                "message": log.message,
                "error_message": log.error,
                "output_json": _truncate_output_json(log.output),
            })

    def _update_task_from_context(self, task_id: str, context: IngestionContext) -> None:
        status = context.status.value if context.status is not None else IngestionStatus.FAILED.value
        chunk_count = len(context.chunks or [])
        updates: Dict = {
            "status": status,
            "chunk_count": chunk_count,
            "completed_at": now_iso(),
            "updated_by": UserContext.get_username(),
        }
        if context.error is not None:
            updates["error_message"] = str(context.error)
        updates["logs_json"] = _dumps(_build_log_summary(context.logs))
        updates["metadata_json"] = _dumps(_build_task_metadata(context))
        self._task_dao.update_by_id(task_id, updates)


def _build_node_order_map(pipeline: PipelineDefinition) -> Dict[str, int]:
    """起始节点 1..n 依连线递增；未连线的节点补尾部序号（对齐 Java buildNodeOrderMap）"""
    order_map: Dict[str, int] = {}
    node_map: Dict[str, Any] = {}
    for node in pipeline.nodes or []:
        if node is None or not node.node_id:
            continue
        node_map.setdefault(node.node_id, node)
    if not node_map:
        return order_map
    referenced = {n.next_node_id for n in node_map.values() if n.next_node_id}
    order = 1
    visited: set = set()
    for node_id in node_map:
        if node_id in referenced:
            continue
        current = node_id
        while current and current not in visited:
            order_map[current] = order
            order += 1
            visited.add(current)
            config = node_map.get(current)
            current = config.next_node_id if config is not None else None
    for node_id in node_map:
        if node_id not in visited:
            order_map[node_id] = order
            order += 1
    return order_map


def _resolve_node_status(log: NodeLog) -> str:
    if not log.success:
        return "failed"
    if log.message and log.message.startswith("Skipped:"):
        return "skipped"
    return "success"


def _build_log_summary(logs: Optional[List[NodeLog]]) -> List[Dict]:
    if not logs:
        return []
    return [
        {
            "nodeId": log.node_id,
            "nodeType": log.node_type,
            "message": log.message,
            "durationMs": log.duration_ms,
            "success": log.success,
            "error": log.error,
            "output": None,
        }
        for log in logs if log is not None
    ]


def _build_task_metadata(context: IngestionContext) -> Dict:
    data: Dict = {}
    if context.metadata:
        data.update(context.metadata)
    if context.keywords:
        data["keywords"] = context.keywords
    if context.questions:
        data["questions"] = context.questions
    return data


def _truncate_output_json(output: Any) -> Optional[str]:
    """output → JSON 串，1MB 截断（对齐 Java truncateOutputJson）"""
    if output is None:
        return None
    raw = _dumps(output)
    if raw is None:
        return None
    if len(raw) <= _OUTPUT_JSON_MAX:
        return raw
    return raw[: _OUTPUT_JSON_MAX - 100] + f"... [输出过大，已截断，原始大小: {len(raw)} 字节]"


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except (ValueError, TypeError):
        return None


def _loads(raw: Optional[str]) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw) or {}
    except (ValueError, TypeError):
        return {}


def _normalize_status(status: Optional[str]) -> Optional[str]:
    if not status:
        return status
    try:
        return IngestionStatus.from_value(status).value
    except ValueError:
        return status


def _normalize_source_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    try:
        return SourceType.from_value(value).value
    except ValueError:
        return value


def _to_vo(task: Dict) -> Dict:
    return {
        "id": str(task["id"]),
        "pipelineId": str(task.get("pipeline_id") or ""),
        "sourceType": _normalize_source_type(task.get("source_type")),
        "sourceLocation": task.get("source_location"),
        "sourceFileName": task.get("source_file_name"),
        "status": _normalize_status(task.get("status")),
        "chunkCount": task.get("chunk_count"),
        "errorMessage": task.get("error_message"),
        "logs": _loads(task.get("logs_json")),
        "metadata": _loads(task.get("metadata_json")),
        "startedAt": task.get("started_at"),
        "completedAt": task.get("completed_at"),
        "createdBy": task.get("created_by"),
        "createTime": task.get("create_time"),
        "updateTime": task.get("update_time"),
    }


def _to_node_vo(node: Dict) -> Dict:
    return {
        "id": str(node["id"]),
        "taskId": str(node.get("task_id") or ""),
        "pipelineId": str(node.get("pipeline_id") or ""),
        "nodeId": node.get("node_id"),
        "nodeType": node.get("node_type"),
        "nodeOrder": node.get("node_order"),
        "status": node.get("status"),
        "durationMs": node.get("duration_ms"),
        "message": node.get("message"),
        "errorMessage": node.get("error_message"),
        "output": _loads(node.get("output_json")),
        "createTime": node.get("create_time"),
        "updateTime": node.get("update_time"),
    }
