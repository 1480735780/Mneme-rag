"""kernel 解析节点异步分发单测：async 解析器被 await，同步解析器走原路径"""
import asyncio

from rag.ingestion.kernel import (
    ChunkEmbeddingService,
    DefaultIngestionKernel,
    DocumentRef,
    IngestionSpec,
)
from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.model import ParagraphBlock, ParsedDocument, Provenance
from rag.ingestion.parser.registry import ParserRegistry
from rag.ingestion.sink import ChunkIndexWriter
from rag.ingestion.splitter.base import ChunkBudget, ChunkingService
from storage.vector.schema import VectorTarget


def _run(coro):
    return asyncio.run(coro)


class _FakeEmbeddingService:
    def __init__(self, dimension=2):
        self._dimension = dimension

    async def embed_batch(self, texts, model):
        return [[0.1] * self._dimension for _ in texts]


class _FakeSink:
    async def replace_document(self, target, doc, chunks):
        pass

    async def delete_document(self, target, doc):
        pass


def _paragraph():
    return ParagraphBlock(Provenance.of_file("a.pdf"), "正文")


class _SyncParser(DocumentParser):
    @property
    def parser_type(self):
        return "sync"

    def supported_mime_types(self):
        return {ParseProfile.FAST: {"text/plain"}}

    def parse_structured(self, content, mime_type=None, options=None):
        return ParsedDocument.of([_paragraph()], {"parser": "sync"})


class _AsyncParser(DocumentParser):
    @property
    def parser_type(self):
        return ParserType.MINERU.value

    def supported_mime_types(self):
        return {ParseProfile.FAST: {"application/pdf"}}

    def parse_structured(self, content, mime_type=None, options=None):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_parse_structured(content, mime_type, options))
        raise RuntimeError("sync entry called inside running loop")

    async def async_parse_structured(self, content, mime_type=None, options=None):
        return ParsedDocument.of([_paragraph()], {"parser": ParserType.MINERU.value})


def _build_kernel(parsers):
    kernel = DefaultIngestionKernel(
        parser_registry=ParserRegistry(parsers),
        chunking_service=ChunkingService(),
        chunk_embedding_service=ChunkEmbeddingService(_FakeEmbeddingService()),
        chunk_index_writer=ChunkIndexWriter([_FakeSink()]),
    )
    return kernel


def _target():
    return VectorTarget(partition="p1", embedding_model="m1", dimension=2)


def _spec():
    return IngestionSpec.of(ParseProfile.FAST, ChunkBudget.whole_document())


def test_async_parser_dispatched_via_async_entry():
    kernel = _build_kernel([_AsyncParser()])
    outcome = _run(kernel.run(DocumentRef("doc-1", "kb-1", "a.pdf"), b"PDF", _spec(), _target()))
    assert outcome.parser_type == ParserType.MINERU.value
    assert outcome.block_count == 1


def test_sync_parser_goes_through_sync_path():
    kernel = _build_kernel([_SyncParser()])
    outcome = _run(kernel.run(DocumentRef("doc-2", "kb-1", "a.txt"), b"txt", _spec(), _target()))
    assert outcome.parser_type == "sync"
    assert outcome.block_count == 1
