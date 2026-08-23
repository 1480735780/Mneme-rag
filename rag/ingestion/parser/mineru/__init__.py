"""
rag.ingestion.parser.mineru - MinerU SaaS 文档解析外接子包

    - properties：MinerUProperties（env 驱动配置）
    - model：MinerUTaskState / MinerUStatus / BatchSubmitRequest / BatchUploadTicket
    - client：MinerUClient（requestUpload / uploadFile / queryResult / downloadZip）
    - polling：MinerUPollingExecutor（异步轮询执行器）
    - unpacker：MinerUResultUnpacker（ZIP → Markdown → Blocks）
    - parser：MinerUDocumentParser（MIME 认领 + 双入口 parse 流程）
"""
from __future__ import annotations

from rag.ingestion.parser.mineru.client import MinerUClient
from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor
from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker

__all__ = [
    "MinerUDocumentParser",
    "MinerUClient",
    "MinerUPollingExecutor",
    "MinerUResultUnpacker",
]


def __getattr__(name: str):
    # parser 模块由后续 Task 逐个补入；此处懒加载，避免 parser 就绪前子包不可导入
    if name == "MinerUDocumentParser":
        from rag.ingestion.parser.mineru.parser import MinerUDocumentParser

        return MinerUDocumentParser
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
