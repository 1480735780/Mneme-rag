# -*- coding: utf-8 -*-
"""
knowledge.versioning.builder - 版本化构建的 attempt 域编排（P9 §6.1–6.2）

职责（构建回调内、存储写入前的编排）：
    1. 0 chunk 直接失败（§6.1.6，PermanentJobError → FAILED_MANUAL）；
    2. 写入前保存 expected_child_count / expected_parent_count（§6.1.4，完整性门槛对账基线）；
    3. 清理同一 job 的上一 attempt（重试场景；worker 只清自己租约下 job 的 attempt，§6.2）；
    4. 为全部块赋 attempt 域键 chunk_key_id（§3.4：children 先编 0..n-1，parents 续编——
       四元组唯一约束要求 index 在 attempt 域内全局唯一）；
    5. 扇出 attempt 域写入（向量 + 关键词 + 关系库 + 注册表随写随登记）。

边界：解析 / 分块 / embedding 复用既有摄取内核（阶段 5 的 UpdateService 接线）；
本模块只管「拿到已向量化载荷之后」的 attempt 域落库。
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from knowledge.dao.update_job import UpdateJobDao
from knowledge.support.chunk_keys import chunk_key_id
from knowledge.versioning.worker import PermanentJobError
from rag.ingestion.index_payload import IndexPayload
from rag.ingestion.sink import ChunkIndexWriter

if TYPE_CHECKING:
    from rag.ingestion.kernel import DocumentRef
    from storage.vector.schema import VectorTarget
    from knowledge.versioning.lease import HeartbeatLease


def assign_attempt_chunk_ids(
    payload: IndexPayload,
    document_id: str,
    revision_id: str,
    build_attempt_id: str,
) -> tuple:
    """为载荷全部块赋 attempt 域主键 chunk_key_id（children 先编、parents 续编）。

    幂等：chunk_id 已等于期望键时跳过（重试重赋值不改变身份）。父块与子块共用同一
    index 序列（§3.4 四元组唯一约束口径），parent_id 引用的父块键随父块一起更新。

    Returns:
        tuple: (expected_child_count, expected_parent_count)
    """
    seq = 0
    for embedded in payload.child_chunks:
        chunk = embedded.chunk
        chunk.chunk_id = chunk_key_id(
            document_id, revision_id, build_attempt_id, seq, chunk.chunk_role or "child"
        )
        chunk.index = seq
        seq += 1
    child_count = seq
    for embedded in payload.parent_chunks:
        chunk = embedded.chunk
        chunk.chunk_id = chunk_key_id(
            document_id, revision_id, build_attempt_id, seq, chunk.chunk_role or "parent"
        )
        chunk.index = seq
        seq += 1
    return child_count, seq - child_count


class VersionedAttemptWriter:
    """attempt 域写入编排器（worker 的构建回调内核；双后端无感知）"""

    def __init__(self, writer: ChunkIndexWriter, job_dao: UpdateJobDao):
        self._writer = writer
        self._job_dao = job_dao

    async def write_attempt(
        self,
        target: "VectorTarget",
        doc: "DocumentRef",
        revision_id: str,
        build_attempt_id: str,
        payload: IndexPayload,
        job_id: str,
        previous_attempt_id: Optional[str] = None,
        lease: Optional["HeartbeatLease"] = None,
    ) -> None:
        """attempt 域落库全流程（§6.1–6.2）；lease 用于长写入的协作式中止点"""
        if lease:
            lease.check()
        if not payload.child_chunks and not payload.parent_chunks:
            # §6.1.6：解析产出 0 个可索引 Chunk 直接失败（FAILED_MANUAL，不进入发布）
            raise PermanentJobError("解析产出 0 个可索引 Chunk（无可索引内容）")

        child_count, parent_count = assign_attempt_chunk_ids(
            payload, doc.doc_id, revision_id, build_attempt_id
        )
        # §6.1.4：写入前保存期望计数（完整性门槛对账基线，§6.3）
        self._job_dao.update_fields(job_id, {
            "expected_child_count": child_count,
            "expected_parent_count": parent_count,
        })

        if previous_attempt_id and previous_attempt_id != build_attempt_id:
            # 重试场景：清理同一 job 的上一 attempt（worker 只清自己 job 的域，§6.2/§11.3）
            if lease:
                lease.check()
            await self._writer.purge_attempt(target.partition, previous_attempt_id)

        if lease:
            lease.check()
        await self._writer.write_attempt(
            target, doc, revision_id, build_attempt_id, payload
        )

    async def purge_attempt(self, collection_name: str, build_attempt_id: str) -> int:
        """attempt 域清理的透传（sweeper / 测试辅助；worker 正常路径经 write_attempt 内置清理）"""
        return await self._writer.purge_attempt(collection_name, build_attempt_id)


def new_build_attempt_id() -> str:
    """新 build attempt id（重试递增/重新生成，§3.3；worker 领取时由 claim 赋值）"""
    return uuid.uuid4().hex
