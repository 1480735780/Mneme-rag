# -*- coding: utf-8 -*-
"""
core.llm.providers.bailian_rerank - 百炼（SiliconFlow 兼容）Rerank 客户端

对应 ragent 的 BaiLianRerankClient.java。

调用 SiliconFlow /v1/rerank 端点，对检索候选按 query 相关度重排序。
完整逻辑：入参去重 → 构建 rerank 请求体 → HTTP 调用 → 解析 output.results
→ 取 index 映射回候选 → 取 relevance_score 覆盖 score → topN 截断 → 不足补齐。
"""

import logging
import math
from typing import Any, Dict, List, Optional

import httpx

from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from core.llm.config.config import ProviderConfig
from core.llm.model.model_target import ModelTarget
from core.llm.schema import RetrievedChunk

from .base_rerank import BaseRerankClient

logger = logging.getLogger(__name__)


class BaiLianRerankClient(BaseRerankClient):
    """
    百炼 / SiliconFlow Rerank 客户端（对应 Java BaiLianRerankClient）。

    调用 /v1/rerank 端点，对候选文档片段按与 query 的相关度重新排序。
    支持：按 id 去重、topN 截断、返回不足时用未命中候选补齐。
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._http_client = http_client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
            ),
        )

    @property
    def provider(self) -> str:
        return "siliconflow"

    # ==================== 接口实现 ====================

    async def rerank(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_n: int,
        target: ModelTarget,
    ) -> List[RetrievedChunk]:
        """对检索候选重排序（对应 Java rerank）。"""
        if not candidates:
            return []

        # 按 id 去重，保留首个（对齐 Java HashSet 语义）
        dedup: List[RetrievedChunk] = []
        seen_ids: set = set()
        for rc in candidates:
            if rc.id not in seen_ids:
                seen_ids.add(rc.id)
                dedup.append(rc)

        # topN 无效或候选数已 ≤ topN → 直接返回去重结果（不调 API）
        if top_n <= 0 or len(dedup) <= top_n:
            return dedup

        return await self._do_rerank(query, dedup, top_n, target)

    # ==================== 模板方法：核心请求逻辑 ====================

    async def _do_rerank(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_n: int,
        target: ModelTarget,
    ) -> List[RetrievedChunk]:
        """构建请求、发送 HTTP、解析 rerank 响应（对应 Java doRerank）。"""
        provider_cfg = self._require_provider(target)
        api_key = self._resolve_api_key(provider_cfg)

        if not api_key:
            raise ModelClientException(
                f"{self.provider} API密钥缺失",
                ModelClientErrorType.UNAUTHORIZED,
            )

        url = self._resolve_url(target)
        body = self._build_body(query, candidates, top_n, target)
        headers = self._build_headers(api_key)
        timeout = self._resolve_timeout(target)

        try:
            response = await self._http_client.post(
                url,
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TransportError as e:
            raise ModelClientException(
                f"{self.provider} rerank 请求失败: {e}",
                ModelClientErrorType.NETWORK_ERROR,
                cause=e,
            ) from e

        if response.status_code >= 400:
            raise ModelClientException(
                f"{self.provider} rerank 请求失败: HTTP {response.status_code}",
                ModelClientErrorType.from_http_status(response.status_code),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as e:
            raise ModelClientException(
                f"{self.provider} 响应解析失败: {e}",
                ModelClientErrorType.INVALID_RESPONSE,
                cause=e,
            ) from e

        return self._extract_results(data, candidates, top_n)

    # ==================== 构建辅助 ====================

    def _build_body(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_n: int,
        target: ModelTarget,
    ) -> Dict[str, Any]:
        """构建 /v1/rerank 请求体（对齐 Java doRerank）。"""
        return {
            "model": target.candidate.model,
            "input": {
                "query": query,
                "documents": [rc.text or "" for rc in candidates],
            },
            "parameters": {
                "top_n": top_n,
                "return_documents": True,
            },
        }

    def _resolve_url(self, target: ModelTarget) -> str:
        """解析 rerank 端点 URL：候选 URL > provider.url + endpoints["rerank"]。"""
        candidate = target.candidate
        if candidate is not None and candidate.url and candidate.url.strip():
            return candidate.url.strip()

        provider_cfg = self._require_provider(target)
        base_url = provider_cfg.url.rstrip("/")
        if not base_url:
            raise ModelClientException(
                f"{self.provider} 提供商基础URL缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        path = provider_cfg.endpoints.get("rerank")
        if not path:
            raise ModelClientException(
                f"{self.provider} 提供商 rerank 端点缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        path = path.strip()
        if base_url.endswith("/") and path.startswith("/"):
            return base_url + path[1:]
        if not base_url.endswith("/") and not path.startswith("/"):
            return base_url + "/" + path
        return base_url + path

    def _extract_results(
        self,
        data: Dict[str, Any],
        candidates: List[RetrievedChunk],
        top_n: int,
    ) -> List[RetrievedChunk]:
        """
        解析 rerank 响应（对齐 Java doRerank 的响应解析）。

        校验 output.results → 逐项取 index 映射回候选 → 取 relevance_score
        覆盖 score → 按 topN 截断；不足时用未命中候选按原序补齐。
        """
        if not isinstance(data, dict) or "output" not in data:
            raise ModelClientException(
                f"{self.provider} rerank 响应缺少 output",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        output = data.get("output")
        if not isinstance(output, dict) or "results" not in output:
            raise ModelClientException(
                f"{self.provider} rerank 响应缺少 results",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        results = output.get("results")
        if not results:
            raise ModelClientException(
                f"{self.provider} rerank results 为空",
                ModelClientErrorType.INVALID_RESPONSE,
            )

        reranked: List[RetrievedChunk] = []
        added_ids: set = set()

        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if idx is None or idx < 0 or idx >= len(candidates):
                continue

            src = candidates[idx]

            # 取 relevance_score 覆盖 score
            score = item.get("relevance_score")
            if score is not None and isinstance(score, (int, float)) and not (math.isnan(score) or math.isinf(score)):
                # 整体拷贝仅覆盖分数（对齐 Java toBuilder().score(score)）
                hit = RetrievedChunk(
                    id=src.id,
                    text=src.text,
                    score=float(score),
                    collection_name=src.collection_name,
                    doc_id=src.doc_id,
                    chunk_index=src.chunk_index,
                    doc_name=src.doc_name,
                )
            else:
                hit = src

            reranked.append(hit)
            added_ids.add(src.id)

            if len(reranked) >= top_n:
                break

        # 不足 topN 时用未命中候选按原序补齐
        if len(reranked) < top_n:
            for rc in candidates:
                if rc.id not in added_ids:
                    reranked.append(rc)
                if len(reranked) >= top_n:
                    break

        return reranked

    # ==================== 内部辅助 ====================

    def _require_provider(self, target: ModelTarget) -> ProviderConfig:
        if target is None or target.provider is None:
            raise ModelClientException(
                f"{self.provider} 提供商配置缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        return target.provider

    def _resolve_api_key(self, provider_cfg: ProviderConfig) -> str:
        if provider_cfg is None:
            return ""
        return (provider_cfg.resolve_api_key() or "").strip()

    def _build_headers(self, api_key: str) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _resolve_timeout(self, target: ModelTarget) -> Optional[float]:
        if target is not None and target.timeout_ms:
            return target.timeout_ms / 1000
        return None
