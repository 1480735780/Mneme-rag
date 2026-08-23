"""MinerUClient 单测（httpx.MockTransport 全离线）"""
import asyncio
import httpx
import pytest

from common.exception.business import ServiceException
from rag.ingestion.parser.mineru.client import MinerUClient
from rag.ingestion.parser.mineru.model import (
    BatchSubmitRequest,
    MinerUStatus,
    MinerUTaskState,
)
from rag.ingestion.parser.mineru.properties import MinerUProperties


def _run(coro):
    return asyncio.run(coro)


def _make_client(handler, **props_kwargs):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    props = MinerUProperties(api_key="sk-test", **props_kwargs)
    return MinerUClient(props, http_client=http), http


class TestRequestUpload:
    def test_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path.endswith("/file-urls/batch")
            assert request.headers["Authorization"] == "Bearer sk-test"
            body = request.read().decode()
            assert '"enable_table":true' in body
            assert '"name":"a.pdf"' in body
            return httpx.Response(
                200,
                json={"code": 0, "msg": "success", "data": {"batch_id": "b1", "file_urls": ["http://up"]}},
            )

        client, _ = _make_client(handler)
        ticket = _run(client.request_upload(BatchSubmitRequest("a.pdf", "doc-1", True, True, True, "ch")))
        assert ticket.batch_id == "b1"
        assert ticket.upload_url == "http://up"

    def test_missing_api_key(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(500))
        http = httpx.AsyncClient(transport=transport)
        client = MinerUClient(MinerUProperties(api_key=""), http_client=http)
        with pytest.raises(ServiceException):
            _run(client.request_upload(BatchSubmitRequest("a.pdf", "doc-1", False, True, True, "ch")))

    def test_missing_batch_id_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"file_urls": ["http://up"]}})

        client, _ = _make_client(handler)
        with pytest.raises(ServiceException):
            _run(client.request_upload(BatchSubmitRequest("a.pdf", "doc-1", False, True, True, "ch")))

    def test_missing_file_urls_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"batch_id": "b1", "file_urls": []}})

        client, _ = _make_client(handler)
        with pytest.raises(ServiceException):
            _run(client.request_upload(BatchSubmitRequest("a.pdf", "doc-1", False, True, True, "ch")))

    def test_nonzero_code_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 1, "msg": "rate limited", "data": {}})

        client, _ = _make_client(handler)
        with pytest.raises(ServiceException):
            _run(client.request_upload(BatchSubmitRequest("a.pdf", "doc-1", False, True, True, "ch")))

    def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="oops")

        client, _ = _make_client(handler)
        with pytest.raises(ServiceException):
            _run(client.request_upload(BatchSubmitRequest("a.pdf", "doc-1", False, True, True, "ch")))


class TestUploadFile:
    def test_success_put(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PUT"
            assert request.url == "http://up/a.pdf"
            assert request.read() == b"pdf-bytes"
            return httpx.Response(200)

        client, _ = _make_client(handler)
        _run(client.upload_file("http://up/a.pdf", b"pdf-bytes"))

    def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden")

        client, _ = _make_client(handler)
        with pytest.raises(ServiceException):
            _run(client.upload_file("http://up/a.pdf", b"pdf-bytes"))


class TestQueryResult:
    def test_done(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.headers["Authorization"] == "Bearer sk-test"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"state": "done", "full_zip_url": "http://z", "err_msg": None}
                        ]
                    },
                },
            )

        client, _ = _make_client(handler)
        status = _run(client.query_result("b1"))
        assert status.state == "done"
        assert status.zip_url == "http://z"
        assert status.error_message is None

    def test_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [{"state": "failed", "full_zip_url": None, "err_msg": "boom"}]
                    },
                },
            )

        client, _ = _make_client(handler)
        status = _run(client.query_result("b1"))
        assert status.failed()
        assert status.error_message == "boom"

    def test_empty_extract_result_means_running(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"extract_result": []}})

        client, _ = _make_client(handler)
        status = _run(client.query_result("b1"))
        assert status.state == "running"

    def test_missing_api_key_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(500))
        http = httpx.AsyncClient(transport=transport)
        client = MinerUClient(MinerUProperties(api_key=""), http_client=http)
        with pytest.raises(ServiceException):
            _run(client.query_result("b1"))

    def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        client, _ = _make_client(handler)
        with pytest.raises(ServiceException):
            _run(client.query_result("b1"))


class TestDownloadZip:
    def test_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "http://z"
            return httpx.Response(200, content=b"ZIPDATA")

        client, _ = _make_client(handler)
        data = _run(client.download_zip("http://z"))
        assert data == b"ZIPDATA"

    def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        client, _ = _make_client(handler)
        with pytest.raises(ServiceException):
            _run(client.download_zip("http://z"))
