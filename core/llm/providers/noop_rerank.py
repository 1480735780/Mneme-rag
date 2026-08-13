# -*- coding: utf-8 -*-
"""
core.llm.providers.noop_rerank - 空实现 Rerank 客户端

对应 ragent 的 NoopRerankClient.java。

不做重排，直接返回前 topN 条（保序截断），provider = "noop"。
用于测试或占位场景。
"""

from typing import List

from core.llm.model.model_target import ModelTarget
from core.llm.schema import RetrievedChunk

from .base_rerank import BaseRerankClient


class NoopRerankClient(BaseRerankClient):
    """空实现 Rerank 客户端（对应 Java NoopRerankClient）。"""

    @property
    def provider(self) -> str:
        return "noop"

    async def rerank(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_n: int,
        target: ModelTarget,
    ) -> List[RetrievedChunk]:
        """不做重排，直接返回前 topN 条（保序截断）。"""
        if not candidates:
            return []
        if top_n <= 0 or len(candidates) <= top_n:
            return list(candidates)
        return list(candidates[:top_n])
