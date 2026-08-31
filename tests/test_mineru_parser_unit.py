"""MinerUDocumentParser 单测（fakes 注入全链路）"""
import asyncio

import pytest

from common.exception.business import ServiceException
from rag.ingestion.parser.base import ParseProfile, ParserType
from rag.ingestion.parser.mineru.model import MinerUStatus
from rag.ingestion.parser.mineru.parser import MinerUDocumentParser
from rag.ingestion.parser.mineru.properties import MinerUProperties
from rag.ingestion.parser.model import ParsedDocument


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    def __init__(self):
        self.uploaded = None

    async def request_upload(self, request):
        return _Ticket("b1", "http://up")

    async def upload_file(self, upload_url, content):
        self.uploaded = content

    async def query_result(self, batch_id):
        return MinerUStatus("done", "http://z", None)

    async def download_zip(self, zip_url):
        return b"ZIPDATA"


class _Ticket:
    def __init__(self, batch_id, upload_url):
        self.batch_id = batch_id
        self.upload_url = upload_url


class _FakePolling:
    def __init__(self):
        self.calls = []

    async def submit_and_await(self, batch_id):
        self.calls.append(batch_id)
        return MinerUStatus("done", "http://z", None)


class _FakeUnpacker:
    async def unpack(self, zip_bytes, source_file, document_id):
        return ParsedDocument.of([], {"parser": ParserType.MINERU.value})


def _parser(**props_kwargs):
    props = MinerUProperties(api_key="sk-test", concurrency_limit=1, **props_kwargs)
    client = _FakeClient()
    polling = _FakePolling()
    unpacker = _FakeUnpacker()
    return (
        MinerUDocumentParser(client, polling, unpacker, props),
        client,
        polling,
        unpacker,
    )


class TestMimeTypes:
    def test_layout_fast_claims(self):
        p, *_ = _parser()
        fast = p.supported_mime_types()[ParseProfile.FAST]
        assert "application/pdf" in fast
        assert "application/msword" in fast
        assert "application/vnd.openxmlformats-officedocument.presentationml.presentation" in fast
        assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in fast

    def test_spreadsheet_fidelity_claims(self):
        p, *_ = _parser()
        fid = p.supported_mime_types()[ParseProfile.FIDELITY]
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in fid
        assert "application/vnd.ms-excel" in fid

    def test_parser_type(self):
        p, *_ = _parser()
        assert p.parser_type == ParserType.MINERU.value


class TestAsyncParse:
    def test_full_flow(self):
        p, client, polling, unpacker = _parser()
        parsed = _run(
            p.async_parse_structured(
                b"PDF", "application/pdf", {"sourceFile": "a.pdf", "documentId": "doc-1"}
            )
        )
        assert client.uploaded == b"PDF"
        assert polling.calls == ["b1"]
        assert parsed.metadata["minerU.batchId"] == "b1"
        assert parsed.metadata["minerU.zipUrl"] == "http://z"
        assert parsed.metadata["parser"] == ParserType.MINERU.value
        assert parsed.metadata["mimeType"] == "application/pdf"

    def test_empty_content_raises(self):
        p, *_ = _parser()
        with pytest.raises(ServiceException):
            _run(p.async_parse_structured(b"", "application/pdf", {}))

    def test_sync_entry_raises_inside_running_loop(self):
        p, *_ = _parser()

        async def _call_sync_entry():
            p.parse_structured(b"PDF", "application/pdf", {})

        with pytest.raises(RuntimeError):
            _run(_call_sync_entry())

    def test_sync_entry_works_without_loop(self):
        # 无运行 loop：asyncio.run 包装走通
        p, client, polling, _ = _parser()
        parsed = p.parse_structured(
            b"PDF", "application/pdf", {"sourceFile": "a.pdf", "documentId": "doc-1"}
        )
        assert parsed.metadata["minerU.batchId"] == "b1"
