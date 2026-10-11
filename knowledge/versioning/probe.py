# -*- coding: utf-8 -*-
"""
knowledge.versioning.probe - 后端能力实测探针（P9 §10.2 / §17.5.2）

KB 启用版本化前不能只读配置声明，必须实际执行一次：
    1. 写入带 revision 的测试数据（固定 probe document_id，便于清理残留）；
    2. 带版本过滤查询并确认只返回测试 revision；
    3. 精确删除测试 attempt。

运行形态（§17.5.2）：异步执行（不放启动同步路径，PG 抖动不能导致服务起不来）；
失败把通道标为 versioning_unavailable 并暴露到健康检查端点（本模块维护状态表）；
通道配置新增或变化时重新校验（重新调用 probe 即可）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from knowledge.support.chunk_keys import chunk_key_id
from knowledge.versioning.snapshot import PROBE_DOCUMENT_ID
from rag.ingestion.index_payload import IndexPayload

# 探针固定标识（残留可按 attempt id 精确清理）
PROBE_REVISION_ID = "__versioning_probe_rev__"
PROBE_ATTEMPT_PREFIX = "__versioning_probe_attempt_"


class VersioningUnavailable(RuntimeError):
    """通道版本化能力不可用（probe 失败；健康检查据此暴露）"""


@dataclass
class ProbeResult:
    """单通道 probe 结果"""

    channel: str
    ok: bool
    detail: str = ""


@dataclass
class VersioningHealth:
    """通道版本化能力状态表（健康检查端点消费）"""

    channels: Dict[str, ProbeResult] = field(default_factory=dict)

    def record(self, result: ProbeResult) -> None:
        self.channels[result.channel] = result

    def is_available(self, channel: str) -> bool:
        result = self.channels.get(channel)
        return result is not None and result.ok

    def unavailable_reason(self, channel: str) -> str:
        result = self.channels.get(channel)
        return result.detail if result else "未执行 probe"


class VersioningCapabilityProbe:
    """
    后端能力实测探针（写入 → 版本过滤查询 → 精确删除）

    Args:
        writer:      ChunkIndexWriter（attempt 域扇出；probe 只走向量+关键词通道即可判定，
                     关系库注册表语义由单测覆盖）
        retriever:   VectorRetrieverService（带版本过滤查询）
        store:       VectorStoreService（write_attempt / purge_attempt）
        target:      probe 用的向量落点（独立 collection，避免污染业务数据）
    """

    PROBE_COLLECTION = "__versioning_probe_col__"

    def __init__(self, store, retriever, writer, target, dimension: int = 1024):
        self._store = store
        self._retriever = retriever
        self._writer = writer
        self._target = target
        self._dimension = dimension

    @staticmethod
    def _probe_vector(dimension: int) -> list:
        """维度匹配的探针向量（真实表 vector(N) 列有维度约束，2 维硬编码进不去）"""
        vec = [0.0] * dimension
        vec[0] = 1.0
        return vec

    async def probe(self, channel: str = "pgvector") -> ProbeResult:
        attempt_id = f"{PROBE_ATTEMPT_PREFIX}{uuid.uuid4().hex[:12]}"
        try:
            # 1. 写入带 revision 的测试数据（chunk id = probe 规范键）
            probe_chunk_id = chunk_key_id(
                PROBE_DOCUMENT_ID, PROBE_REVISION_ID, attempt_id, 0, "child"
            )
            from core.llm.schema import ChunkData, ChunkMetadata, EmbeddedChunk

            payload = IndexPayload(
                child_chunks=[
                    EmbeddedChunk(
                        chunk=ChunkData(
                            chunk_id=probe_chunk_id, index=0,
                            content="__versioning_probe__",
                            embedding_text="__versioning_probe__",
                            chunk_role="child", metadata=ChunkMetadata(),
                        ),
                        embedding=self._probe_vector(self._dimension),
                    )
                ],
                parent_chunks=[],
            )
            from types import SimpleNamespace

            probe_doc = SimpleNamespace(doc_id=PROBE_DOCUMENT_ID, kb_id="__versioning_probe_kb__")
            await self._writer.write_attempt(
                self._target, probe_doc, PROBE_REVISION_ID, attempt_id, payload
            )

            # 2. 带版本过滤查询：只返回测试 attempt（不含探针集合外的行）
            from rag.retrieval.schema import RetrieveRequest

            results = await self._retriever.retrieve_by_vector(
                self._probe_vector(self._dimension),
                RetrieveRequest(
                    query="probe", top_k=10,
                    collection_names=[self.PROBE_COLLECTION],
                    revision_ids=[PROBE_REVISION_ID],
                    committed_attempt_ids=[attempt_id],
                ),
            )
            got_ids = {r.id for r in results}
            if got_ids != {probe_chunk_id}:
                return ProbeResult(
                    channel, False,
                    f"版本过滤查询返回 {got_ids or '空'}，期望 {{'{probe_chunk_id}'}}",
                )

            # 3. 精确删除测试 attempt
            removed = await self._store.purge_attempt(self.PROBE_COLLECTION, attempt_id)
            if removed < 1:
                return ProbeResult(channel, False, f"probe attempt 清理失败（removed={removed}）")
            return ProbeResult(channel, True, "写入/过滤查询/精确删除三步全部通过")
        except Exception as exc:  # noqa: BLE001 - probe 的任何失败都=能力不可用
            return ProbeResult(channel, False, f"{type(exc).__name__}: {exc}")
