"""
离线知识入库 CLI（P1 端到端驱动）

一条命令驱动完整入库链路：加载 → 解析 → 切分 → 向量化 → 入库。
加载/解析/切分/向量化/入库全部复用 rag/ingestion 与 storage/vector 的业务组件，
本脚本只做参数解析与流程编排。

用法（在项目根目录执行）：
    # 本地文件入库
    python scripts/ingest.py --file README.md --doc-id doc-1 --kb-id kb-1

    # HTTP URL 入库
    python scripts/ingest.py --url https://example.com/a.md --doc-id doc-2 --kb-id kb-1

    # 指定分区 / 嵌入模型 / 维度，并入库后检索验证
    python scripts/ingest.py --file README.md --doc-id doc-1 --kb-id kb-1 \
        --partition kb_employee_policy --model qwen-embedding --verify "项目是什么？"

对应 ragent 源码：
    - bootstrap 模块的文档上传/入库 CLI 入口（知识库入库链路）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional

# 允许从项目根目录执行：core / rag / storage 包依赖项目根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm.config.config import AIModelConfig, load_config_from_yaml
from core.llm.embedding import EmbeddingService, RoutingEmbeddingService
from core.llm.model.health_store import ModelHealthStore
from core.llm.model.routing_executor import RoutingExecutor
from core.llm.model.selector import ModelSelector
from core.llm.providers.ollama_embedding import OllamaEmbeddingClient
from core.llm.providers.siliconflow_embedding import SiliconFlowEmbeddingClient
from rag.ingestion.kernel import (
    ChunkEmbeddingService,
    DefaultIngestionKernel,
    DocumentRef,
    IngestionOutcome,
    IngestionSpec,
)
from rag.ingestion.loader import (
    DocumentLoader,
    DocumentSource,
    HttpUrlFetcher,
    LocalFileFetcher,
    SourceType,
)
from rag.ingestion.parser import (
    MarkdownDocumentParser,
    ParserRegistry,
    TextDocumentParser,
)
from rag.ingestion.sink import ChunkIndexWriter, VectorStoreSink
from rag.ingestion.splitter import ChunkingService
from rag.retrieval.schema import RetrieveRequest
from storage.vector.in_memory import InMemoryVectorStore
from storage.vector.schema import VectorTarget

DEFAULT_CONFIG = "core/llm/config/ai.yaml"


def build_embedding_service(config: AIModelConfig) -> RoutingEmbeddingService:
    """按 ai.yaml 构建路由式向量化服务：已实现 provider 的客户端全部注册"""
    health = ModelHealthStore(
        failure_threshold=config.selection.failure_threshold,
        open_duration_ms=config.selection.open_duration_ms,
    )
    selector = ModelSelector(config, health)
    executor = RoutingExecutor(health)
    clients = [
        client
        for cand in config.embedding.candidates
        if cand.enabled
        for client in [embedding_client_for(cand.provider)]
        if client is not None
    ]
    if not clients:
        raise ValueError(
            "没有可用的 Embedding 客户端：请配置 ollama / siliconflow 任一 provider"
            "（当前已实现 provider：ollama、siliconflow）"
        )
    return RoutingEmbeddingService(selector, executor, clients)


def embedding_client_for(provider: str):
    """provider → 具体 Embedding 客户端；未实现的 provider 返回 None"""
    if provider == "ollama":
        return OllamaEmbeddingClient()
    if provider == "siliconflow":
        return SiliconFlowEmbeddingClient()
    return None


def resolve_dimension(config: AIModelConfig, model: str) -> int:
    """从配置里嵌入模型候选取维度；查不到即报错，不静默用默认"""
    for cand in config.embedding.candidates:
        if cand.id == model and cand.dimension:
            return cand.dimension
    raise ValueError(f"配置中找不到嵌入模型 [{model}] 及其维度，请用 --dimension 显式指定")


def build_ingestion_chain(embedding_service: EmbeddingService):
    """组装五步链路：解析器注册表 → 切分 → 向量化 → 落库扇出"""
    registry = ParserRegistry([TextDocumentParser(), MarkdownDocumentParser()])
    registry.self_check()

    store = InMemoryVectorStore(embedding_service)
    writer = ChunkIndexWriter([VectorStoreSink(store)])

    kernel = DefaultIngestionKernel(
        parser_registry=registry,
        chunking_service=ChunkingService(),
        chunk_embedding_service=ChunkEmbeddingService(embedding_service),
        chunk_index_writer=writer,
    )
    return kernel, store


def parse_source(file_path: Optional[str], url: Optional[str]) -> DocumentSource:
    if bool(file_path) == bool(url):
        raise ValueError("必须且只能指定 --file 或 --url 之一")
    if file_path:
        return DocumentSource(SourceType.FILE, str(Path(file_path)), Path(file_path).name)
    return DocumentSource(SourceType.URL, url)


def report(outcome: IngestionOutcome) -> None:
    """打印摄取结果：MIME / 解析器 / 块数 / 各阶段耗时"""
    t = outcome.timings
    print("入库完成：")
    print(f"  MIME       : {outcome.mime_type}")
    print(f"  解析器     : {outcome.parser_type}")
    print(f"  Block 数   : {outcome.block_count}")
    print(f"  Chunk 数   : {outcome.chunk_count()}")
    print(
        f"  耗时(ms)   : parse={t.parse_millis} chunk={t.chunk_millis} "
        f"embed={t.embed_millis} index={t.index_millis}"
    )


async def main(args: argparse.Namespace) -> None:
    config = load_config_from_yaml(args.config)
    embedding_service = build_embedding_service(config)
    kernel, store = build_ingestion_chain(embedding_service)

    # 加载（策略路由：本地文件 / HTTP URL）
    loader = DocumentLoader([LocalFileFetcher(), HttpUrlFetcher()])
    source = parse_source(args.file, args.url)
    fetched = await loader.load(source)

    # 落点：分区默认取 kb_id，模型默认取配置默认嵌入模型
    partition = args.partition or args.kb_id
    model = args.model or config.embedding.default_model
    if not model:
        raise ValueError("未指定 --model，且配置里没有默认嵌入模型")
    dimension = args.dimension or resolve_dimension(config, model)
    target = VectorTarget(partition=partition, embedding_model=model, dimension=dimension)

    doc = DocumentRef(
        doc_id=args.doc_id,
        kb_id=args.kb_id,
        filename=fetched.file_name,
    )

    # 执行五步：identity → parse → chunk → embed → index
    outcome = await kernel.run(doc, fetched.content, IngestionSpec.defaults(), target)
    report(outcome)

    # 入库后检索验证（可选）
    if args.verify:
        hits = await store.retrieve(
            RetrieveRequest(query=args.verify, top_k=3, collection_name=partition)
        )
        print("检索验证：")
        if not hits:
            print("  未命中任何块")
        for hit in hits:
            print(f"  [{hit.score:.3f}] {hit.text[:60]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="离线知识入库：加载 → 解析 → 切分 → 向量化 → 入库")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="本地文件路径")
    source.add_argument("--url", help="HTTP/HTTPS 文档地址")
    parser.add_argument("--doc-id", required=True, help="文档 ID（决定资产归属与落库归属）")
    parser.add_argument("--kb-id", required=True, help="所属知识库 ID")
    parser.add_argument("--partition", help="向量分区（默认取 --kb-id）")
    parser.add_argument("--model", help="嵌入模型 ID（默认取配置默认嵌入模型）")
    parser.add_argument("--dimension", type=int, help="向量维度（默认从配置读取）")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"ai.yaml 路径（默认 {DEFAULT_CONFIG}）")
    parser.add_argument("--verify", help="入库后按该查询语句检索验证")
    return parser


if __name__ == "__main__":
    asyncio.run(main(build_parser().parse_args()))
