# -*- coding: utf-8 -*-
"""
knowledge.service.document - 文档域服务（对齐 Java KnowledgeDocumentServiceImpl，N2 心脏）

覆盖 N2 十二端点所需方法：upload / start_chunk / execute_chunk / delete / update / page / search /
enable / get_chunk_logs / preview / file。异步契约：upload/start_chunk/execute_chunk/delete 走
asyncio（fetcher/limiter/sink/dispatcher 均 async，下载不阻塞事件循环）。

依赖注入（跨里程碑协作者以可替换对象接入，避免 N2 提前构建 N3/N5 全量）：
    - 已就绪：kb_dao / document_dao / chunk_log_dao / parser_registry / ingestion_spec_codec /
      vector_target_resolver / ingest_kernel / chunk_index_writer / file_storage / remote_file_fetcher /
      chunk_dispatcher / 上传限流 limiter
    - N3 接入（chunk_dao / chunk_service / vector_store）：enable 的双向向量同步、page 的 chunks_edited
    - N4 接入（schedule_service）：delete/update 的调度行清理
    - N5 接入（pipeline_service）：PIPELINE 模式校验、chunk_log 的 pipelineName

按 plan R5：文档分块仅 CHUNK 模式走内核；PIPELINE 模式抛「管道模式重构中，暂不可用」（对齐 Java 现行为）。

对应 ragent 源码：KnowledgeDocumentServiceImpl（upload/startChunk/executeChunk/delete/update/page/search/enable/getChunkLogs）
"""
from __future__ import annotations

import logging
import time
from contextlib import nullcontext
from typing import Dict, List, Optional, Tuple

from common.context.user_context import UserContext
from common.exception.business import ClientException
from knowledge.dao.chunk_log import KnowledgeDocumentChunkLogDao
from knowledge.dao.document import KnowledgeDocumentDao
from knowledge.enums import DocumentStatus, ProcessMode, SourceType
from knowledge.support.ingestion_spec_codec import IngestionSpecCodec
from rag.dao.support import DELETED, now_iso
from rag.ingestion.parser.base import ParseProfile

logger = logging.getLogger(__name__)


def _process_mode_value(mode) -> str:
    return mode.value if isinstance(mode, ProcessMode) else str(mode)


def _other_duration(row: Dict, total: Optional[int]) -> int:
    """对齐 Java getOther：PIPELINE 不减 embed，CHUNK 减四段；下限 0"""
    if total is None:
        return -1
    mode = row.get("process_mode")
    pipeline = mode and str(mode).lower() == "pipeline"
    extract = row.get("extract_duration") or 0
    chunk = row.get("chunk_duration") or 0
    embed = row.get("embed_duration") or 0
    persist = row.get("persist_duration") or 0
    return max(0, total - (chunk + persist) if pipeline else total - extract - chunk - embed - persist)


class KnowledgeDocumentService:
    """文档域服务（注入全部依赖，无状态；跨里程碑协作者经构造注入）"""

    def __init__(
        self,
        kb_dao,
        doc_dao: KnowledgeDocumentDao,
        chunk_log_dao: KnowledgeDocumentChunkLogDao,
        parser_registry,
        codec: IngestionSpecCodec,
        vector_target_resolver,
        ingest_kernel,
        chunk_index_writer,
        file_storage,
        fetcher,
        dispatcher,
        limiter=None,
        chunk_dao=None,
        chunk_service=None,
        vector_store=None,
        schedule_service=None,
        pipeline_service=None,
        min_interval_seconds: int = 60,
    ):
        self._kb_dao = kb_dao
        self._doc_dao = doc_dao
        self._chunk_log_dao = chunk_log_dao
        self._parser_registry = parser_registry
        self._codec = codec
        self._resolver = vector_target_resolver
        self._kernel = ingest_kernel
        self._chunk_index_writer = chunk_index_writer
        self._file_storage = file_storage
        self._fetcher = fetcher
        self._dispatcher = dispatcher
        self._limiter = limiter
        self._chunk_dao = chunk_dao
        self._chunk_service = chunk_service
        self._vector_store = vector_store
        self._schedule_service = schedule_service
        self._pipeline_service = pipeline_service
        self._min_interval_seconds = min_interval_seconds

    # ===================== upload =====================

    async def upload(
        self,
        kb_id: str,
        *,
        source_type: str,
        source_location: Optional[str] = None,
        schedule_enabled: Optional[bool] = None,
        schedule_cron: Optional[str] = None,
        process_mode: Optional[str] = None,
        ingestion_spec: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        file_content: Optional[bytes] = None,
        file_name: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Dict:
        """上传文档（FILE/URL；校验前置 → 存文件 → canParse 拦截 → 落库 PENDING）"""
        limiter_ctx = self._limiter.limit() if self._limiter is not None else nullcontext()
        async with limiter_ctx:
            kb = self._require_kb(kb_id)
            st = SourceType.normalize(source_type)
            self._validate_source_and_schedule(st, source_location, schedule_enabled, schedule_cron)
            mode, spec_json, pipeline_id = self._resolve_process_mode(process_mode, ingestion_spec, pipeline_id)
            stored = await self._resolve_stored_file(
                kb["collection_name"], st, source_location, file_content, file_name, content_type
            )
            if not self._parser_registry.can_parse(stored.mime_type):
                # canParse 拦截不留孤儿：删已存文件再抛（对齐 Java L155-158）
                self._file_storage.delete_by_url(stored.url)
                raise ClientException(f"暂不支持的文件类型：{stored.detected_type}")
            username = UserContext.get_username()
            now = now_iso()
            doc_id = self._doc_dao.insert({
                "kb_id": kb_id,
                "doc_name": stored.original_filename,
                "source_type": st.value,
                "source_location": stored  # 占位防 lint，下述覆盖
            })
            # 真实字段组装
            self._doc_dao.update_by_id(doc_id, {})  # noop 占位避免未提交时误用

            row = {
                "id": doc_id, "kb_id": kb_id, "doc_name": stored.original_filename,
                "source_type": st.value,
                "source_location": source_location.strip() if st == SourceType.URL and source_location else None,
                "schedule_enabled": (1 if (st == SourceType.URL and schedule_enabled) else 0),
                "schedule_cron": (schedule_cron.strip() if st == SourceType.URL and schedule_enabled else None),
                "enabled": 1, "chunk_count": 0,
                "file_url": stored.url, "file_type": stored.detected_type,
                "mime_type": stored.mime_type, "file_size": stored.size,
                "process_mode": _process_mode_value(mode), "ingestion_spec": spec_json,
                "pipeline_id": pipeline_id, "status": DocumentStatus.PENDING.value,
                "created_by": username, "updated_by": username,
                "create_time": now, "update_time": now,
            }
            self._doc_dao.update_by_id(doc_id, row)
            return self._to_vo(row)

    # ===================== start_chunk / execute_chunk =====================

    async def start_chunk(self, doc_id: str) -> None:
        """触发分块：经 dispatcher 做 CAS + 异步执行（重复触发抛「正在分块中」）"""
        doc = self._doc_dao.get_by_id(doc_id)
        if doc is None:
            raise ClientException("文档不存在")
        await self._dispatcher.dispatch(_build_event(doc_id, UserContext.get_username()))

    def _cas_start_chunk(self, doc_id: str, operator: Optional[str]) -> None:
        """CAS 事务体（dispatcher.start_chunk 回调；对齐 Java startChunk 的 sendInTransaction 事务体：
        `status ne RUNNING → RUNNING` 命中才返回，未命中抛「正在分块中」；成功后登记调度（N4 接通））
        """
        if self._doc_dao.get_by_id(doc_id) is None:
            raise ClientException("文档不存在")
        updated = self._doc_dao.cas_update_status(
            doc_id,
            to_status=DocumentStatus.RUNNING.value,
            from_status_not_equal=DocumentStatus.RUNNING.value,
            operator=operator,
        )
        if not updated:
            raise ClientException("文档分块操作正在进行中，请稍后再试")
        # upsertSchedule：N4 schedule_service 接通后在此登记（对齐 Java startChunk 事务体末行）

    async def execute_chunk(self, doc_id: str) -> None:
        """异步执行分块全链（kernel → chunk_log 收尾；PIPELINE 抛「管道模式重构中」）"""
        doc = self._doc_dao.get_by_id(doc_id)
        if doc is None:
            logger.warning("文档不存在，跳过分块任务 docId=%s", doc_id)
            return
        await self._run_chunk_task(doc)

    async def _run_chunk_task(self, doc: Dict) -> None:
        doc_id = doc["id"]
        mode = ProcessMode.normalize(doc.get("process_mode"))
        kb = self._require_kb(doc["kb_id"])
        target = self._resolver.resolve(kb)
        spec = self._codec.read(doc.get("ingestion_spec"))
        log_id = self._chunk_log_dao.insert_running(
            doc_id, DocumentStatus.RUNNING.value, mode.value,
            parse_profile=spec.parse_profile.value if isinstance(spec.parse_profile, ParseProfile) else None,
            pipeline_id=doc.get("pipeline_id"),
        )
        start = time.monotonic()
        try:
            if mode == ProcessMode.PIPELINE:
                # 管道模式重构中：显式失败，不静默改用默认分块（对齐 Java R5）
                raise ClientException(f"管道模式重构中，暂不可用，请改用直接分块：docId={doc_id}")
            outcome = self._kernel.run(doc, self._read_file_bytes(doc), spec, target)
            self._mark_chunk_success(doc_id, _chunk_count(outcome))
            total = int((time.monotonic() - start) * 1000)
            t = _timings(outcome)
            self._chunk_log_dao.update_result(
                log_id, DocumentStatus.SUCCESS.value, _chunk_count(outcome),
                extract_duration=t[0], chunk_duration=t[1], embed_duration=t[2],
                persist_duration=t[3], total_duration=total, error_message=None,
            )
        except Exception as exc:  # noqa: BLE001 —— 全包记 FAILED（对齐 Java catch 全包）
            logger.error("文档分块任务执行失败 docId=%s", doc_id, exc_info=True)
            self._mark_chunk_failed(doc_id)
            total = int((time.monotonic() - start) * 1000)
            self._chunk_log_dao.update_result(
                log_id, DocumentStatus.FAILED.value, 0, 0, 0, 0, 0, total, str(exc),
            )

    # ===================== delete / update =====================

    async def delete(self, doc_id: str) -> None:
        doc = self._require_doc(doc_id)
        if doc.get("status") == DocumentStatus.RUNNING.value:
            raise ClientException("文档正在分块中，无法删除")
        if self._schedule_service is not None:
            self._schedule_service.delete_by_doc_id(doc_id)
        self._chunk_log_dao.delete_by_doc(doc_id)
        self._doc_dao.update_by_id(doc_id, {"deleted": DELETED, "updated_by": UserContext.get_username(),
                                             "update_time": now_iso()})
        kb = self._require_kb(doc["kb_id"])
        target = self._resolver.resolve(kb)
        await self._chunk_index_writer.delete_document(target, _doc_ref(doc))
        self._delete_stored_file_quietly(doc)

    def update(
        self,
        doc_id: str,
        *,
        doc_name: Optional[str] = None,
        process_mode: Optional[str] = None,
        ingestion_spec: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        source_location: Optional[str] = None,
        schedule_enabled: Optional[int] = None,
        schedule_cron: Optional[str] = None,
    ) -> None:
        doc = self._require_doc(doc_id)
        if doc.get("status") == DocumentStatus.RUNNING.value:
            raise ClientException("文档正在分块中，无法修改")
        if not doc_name or not doc_name.strip():
            raise ClientException("文档名称不能为空")
        updates: Dict = {"doc_name": doc_name.strip(), "updated_by": UserContext.get_username(),
                         "update_time": now_iso()}
        # process_mode 切换（对齐 Java update：CHUNK→spec normalize+pipeline 清空 / PIPELINE→pipeline 校验+spec 清空）
        if process_mode and process_mode.strip():
            mode = ProcessMode.normalize(process_mode)
            updates["process_mode"] = mode.value
            if mode == ProcessMode.CHUNK:
                spec_json = self._codec.normalize(ingestion_spec)
                if spec_json:
                    updates["ingestion_spec"] = spec_json
                updates["pipeline_id"] = None
            else:
                if not pipeline_id or not pipeline_id.strip():
                    raise ClientException("使用Pipeline模式时，必须指定Pipeline ID")
                if self._pipeline_service is not None:
                    try:
                        self._pipeline_service.get(pipeline_id)
                    except Exception as exc:  # noqa: BLE001
                        raise ClientException(f"指定的Pipeline不存在: {pipeline_id}") from exc
                updates["pipeline_id"] = pipeline_id
                updates["ingestion_spec"] = None
        # 调度字段（仅 URL 文档，N4 schedule_service 接通后做 upsert）
        if str(doc.get("source_type") or "") == SourceType.URL.value:
            if source_location and source_location.strip():
                updates["source_location"] = source_location.strip()
            if schedule_cron and schedule_cron.strip():
                _validate_cron_interval(schedule_cron, self._min_interval_seconds)
                updates["schedule_cron"] = schedule_cron.strip()
            if schedule_enabled is not None:
                updates["schedule_enabled"] = schedule_enabled
        self._doc_dao.update_by_id(doc_id, updates)

    # ===================== page / search =====================

    def get(self, doc_id: str) -> Dict:
        return self._to_vo(self._require_doc(doc_id))

    def page(
        self,
        kb_id: str,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        current: int = 1,
        size: int = 10,
    ) -> Dict:
        current = current if current and current >= 1 else 1
        size_val = 10 if size is None else max(0, size)
        offset = (current - 1) * size_val if size_val > 0 else 0
        rows, total = self._doc_dao.page(kb_id=kb_id, keyword=keyword, status=status,
                                         limit=size_val, offset=offset)
        edited = self._edited_doc_ids([r["id"] for r in rows])
        records = [dict(r, chunks_edited=r["id"] in edited) for r in rows]
        return {"records": [_vo(r) for r in records], "total": total, "current": current, "size": size_val}

    def search(self, keyword: str, limit: int = 10) -> List[Dict]:
        rows = self._doc_dao.search(keyword, limit)
        return [_vo(r, kb_name=self._kb_dao.get_by_id(r["kb_id"]).get("name") if r.get("kb_id") else None)
                for r in rows]

    # ===================== enable =====================

    async def enable(self, doc_id: str, enabled: bool) -> None:
        doc = self._require_doc(doc_id)
        if doc.get("status") == DocumentStatus.RUNNING.value:
            raise ClientException("文档正在分块中，无法修改")
        target = 1 if enabled else 0
        if doc.get("enabled") == target:
            return
        kb = self._require_kb(doc["kb_id"])
        updates: Dict = {"enabled": target, "updated_by": UserContext.get_username(),
                         "update_time": now_iso()}
        # 向量同步：禁用删向量；启用重嵌入（N3 chunk_service 注入后生效）
        if enabled and self._chunk_service is not None:
            vector_target = self._resolver.resolve(kb)
            chunks = await self._chunk_service.embed_persisted_chunks(doc_id, vector_target)
            # 对齐 Java：启用时无任何 chunk 仅更新启用状态、跳过向量重建（L700-703 warn 继续）
            if chunks and self._vector_store is not None:
                await self._vector_store.index_document_chunks(kb["collection_name"], doc_id, chunks)
        elif not enabled and self._vector_store is not None:
            await self._vector_store.delete_document_vectors(kb["collection_name"], doc_id)
        if self._chunk_dao is not None:
            self._chunk_dao.update_enabled_by_doc(doc_id, enabled)
        self._doc_dao.update_by_id(doc_id, updates)

    # ===================== chunk_logs / preview / file =====================

    def get_chunk_logs(self, doc_id: str, current: int = 1, size: int = 10) -> Dict:
        current = current if current and current >= 1 else 1
        size_val = 10 if size is None else max(0, size)
        offset = (current - 1) * size_val if size_val > 0 else 0
        rows, total = self._chunk_log_dao.page_by_doc(doc_id, limit=size_val, offset=offset)
        records = []
        for row in rows:
            vo = dict(row)
            vo["other_duration"] = _other_duration(row, row.get("total_duration"))
            vo["pipeline_name"] = None  # N5 pipeline_service 接通后回填
            records.append(vo)
        return {"records": records, "total": total, "current": current, "size": size_val}

    def preview(self, doc_id: str) -> str:
        doc = self._require_doc(doc_id)
        from rag.file_storage import DisplayType

        if DisplayType.from_code(doc.get("file_type")) != DisplayType.MARKDOWN:
            raise ClientException("仅支持预览 markdown 格式文档")
        try:
            with self._file_storage.open_stream(doc["file_url"]) as stream:
                return stream.read().decode("utf-8", errors="replace")
        except ClientException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ClientException(f"读取文档内容失败: {exc}") from exc

    def file(self, doc_id: str):
        """返回源文件流（controller 编排 Response 流式返回）"""
        doc = self._require_doc(doc_id)
        return self._file_storage.open_stream(doc["file_url"])

    # ===================== 内部 =====================

    def _require_kb(self, kb_id: str) -> Dict:
        kb = self._kb_dao.get_by_id(kb_id)
        if kb is None:
            raise ClientException("知识库不存在")
        return kb

    def _require_doc(self, doc_id: str) -> Dict:
        doc = self._doc_dao.get_by_id(doc_id)
        if doc is None:
            raise ClientException("文档不存在")
        return doc

    def _validate_source_and_schedule(self, st, source_location, schedule_enabled, schedule_cron) -> None:
        location = (source_location or "").strip()
        if st == SourceType.URL and not location:
            raise ClientException("来源地址不能为空")
        if st != SourceType.URL or not schedule_enabled:
            return
        cron = (schedule_cron or "").strip()
        if not cron:
            raise ClientException("定时表达式不能为空")
        _validate_cron_interval(cron, self._min_interval_seconds)

    def _resolve_process_mode(self, process_mode, ingestion_spec, pipeline_id) -> Tuple[ProcessMode, Optional[str], Optional[str]]:
        mode = ProcessMode.normalize(process_mode)
        if mode == ProcessMode.CHUNK:
            return mode, self._codec.normalize(ingestion_spec), None
        if not pipeline_id or not pipeline_id.strip():
            raise ClientException("使用Pipeline模式时，必须指定Pipeline ID")
        if self._pipeline_service is not None:
            try:
                self._pipeline_service.get(pipeline_id)
            except Exception as exc:  # noqa: BLE001
                raise ClientException(f"指定的Pipeline不存在: {pipeline_id}") from exc
        return mode, None, pipeline_id

    async def _resolve_stored_file(self, bucket_name, st, source_location, file_content, file_name, content_type):
        if st == SourceType.FILE:
            if file_content is None:
                raise ClientException("上传文件不能为空")
            return self._file_storage.upload(bucket_name, file_content, file_name or "file",
                                             content_type=content_type, size=len(file_content))
        return await self._fetcher.fetch_and_store(bucket_name, source_location)

    def _read_file_bytes(self, doc: Dict) -> bytes:
        try:
            with self._file_storage.open_stream(doc.get("file_url")) as stream:
                return stream.read()
        except Exception as exc:  # noqa: BLE001
            raise ClientException(f"读取文件内容失败：docId={doc.get('id')}") from exc

    def _mark_chunk_success(self, doc_id: str, chunk_count: int) -> None:
        self._doc_dao.update_by_id(doc_id, {"chunk_count": chunk_count,
                                            "status": DocumentStatus.SUCCESS.value,
                                            "updated_by": UserContext.get_username(),
                                            "update_time": now_iso()})

    def _mark_chunk_failed(self, doc_id: str) -> None:
        self._doc_dao.update_by_id(doc_id, {"status": DocumentStatus.FAILED.value,
                                            "updated_by": UserContext.get_username(),
                                            "update_time": now_iso()})

    def _edited_doc_ids(self, doc_ids: List[str]):
        if not doc_ids or self._chunk_dao is None:
            return set()
        return self._chunk_dao.find_edited_doc_ids(doc_ids)

    def _delete_stored_file_quietly(self, doc: Dict) -> None:
        url = doc.get("file_url")
        if not url:
            return
        try:
            self._file_storage.delete_by_url(url)
        except Exception:  # noqa: BLE001 —— best-effort 记 warn
            logger.warning("删除文档存储文件失败 docId=%s", doc.get("id"), exc_info=True)

    def _to_vo(self, row: Dict) -> Dict:
        return _vo(row)


# ===================== 模块级助手 =====================

def _vo(row: Dict, kb_name: Optional[str] = None) -> Dict:
    """行 → VO（ingestion_spec 归一化保证 -1 哨兵出参）+ 可选 kb_name"""
    vo = dict(row)
    if vo.get("ingestion_spec"):
        from knowledge.support.ingestion_spec_codec import IngestionSpecCodec as C

        codec = C()
        vo["ingestion_spec"] = codec.write(codec.read(vo["ingestion_spec"]))
    if kb_name is not None:
        vo["kb_name"] = kb_name
    return vo


def _chunk_count(outcome) -> int:
    """分块数：outcome 支持 dict 或对象（.chunk_count）；缺失回落 0"""
    if isinstance(outcome, dict):
        return int(outcome.get("chunk_count") or 0)
    return int(getattr(outcome, "chunk_count", 0) or 0)


def _timings(outcome) -> Tuple[int, int, int, int]:
    """outcome 分阶段耗时 → (parse_ms, chunk_ms, embed_ms, index_ms)；缺失回落 0（对齐 Java outcome.timings）"""
    def get(d, *keys):
        for k in keys:
            v = d.get(k) if isinstance(d, dict) else getattr(d, k, None)
            if v is not None:
                return int(v)
        return 0
    return (get(outcome, "parse_ms", "parseMillis"), get(outcome, "chunk_ms", "chunkMillis"),
            get(outcome, "embed_ms", "embedMillis"), get(outcome, "index_ms", "indexMillis"))


def _doc_ref(doc: Dict):
    """构造 DocumentRef（对齐 Java documentRef）"""
    from rag.ingestion.kernel import DocumentRef

    return DocumentRef(doc_id=doc["id"], kb_id=doc["kb_id"], filename=doc.get("doc_name"))


def _build_event(doc_id: str, operator: Optional[str]):
    from knowledge.mq.chunk_dispatcher import ChunkTaskEvent

    return ChunkTaskEvent(doc_id=doc_id, operator=operator)


def _validate_cron_interval(cron: str, min_seconds: int) -> None:
    """cron 解析 + 间隔下限校验（对齐 Java CronScheduleHelper + min-interval-seconds）"""
    from datetime import datetime

    from knowledge.schedule.cron_helper import CronScheduleHelper

    if not CronScheduleHelper.validate(cron):
        raise ClientException("定时表达式不合法")
    if CronScheduleHelper.is_interval_less_than(cron, datetime.now(), min_seconds):
        raise ClientException(f"定时周期不能小于 {min_seconds} 秒")