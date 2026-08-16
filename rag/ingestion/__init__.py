"""
rag.ingestion - 离线入库链路

    - loader：文档加载器（SourceType 路由 + LocalFile/HttpUrl fetcher）
    - kernel：摄取内核（五步骨架 identity→parse→chunk→embed→index）
    - sink：Chunk 落库端口（ChunkSink + ChunkIndexWriter 扇出）
    - parser：文档解析器（接口 + 注册表 + Text/Markdown 实现）
    - splitter：文本切分器（ChunkBudget + ChunkingService + TextSplitter）
"""
from rag.ingestion.kernel import (
    ChunkEmbeddingService,
    DefaultIngestionKernel,
    DocumentRef,
    IngestionKernel,
    IngestionOutcome,
    IngestionSpec,
    IngestionTimings,
    MimeTypeDetector,
)
from rag.ingestion.loader import (
    DocumentFetcher,
    DocumentLoader,
    DocumentSource,
    FetchResult,
    HttpUrlFetcher,
    LocalFileFetcher,
    SourceType,
)
from rag.ingestion.sink import ChunkIndexWriter, ChunkSink

__all__ = [
    "ChunkEmbeddingService",
    "DefaultIngestionKernel",
    "DocumentRef",
    "IngestionKernel",
    "IngestionOutcome",
    "IngestionSpec",
    "IngestionTimings",
    "MimeTypeDetector",
    "DocumentFetcher",
    "DocumentLoader",
    "DocumentSource",
    "FetchResult",
    "HttpUrlFetcher",
    "LocalFileFetcher",
    "SourceType",
    "ChunkIndexWriter",
    "ChunkSink",
    "VectorStoreSink",
]
