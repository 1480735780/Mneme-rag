"""MinerUPollingExecutor 单测（fake client，异步轮询）"""
import asyncio

import pytest

from common.exception.business import ServiceException
from rag.ingestion.parser.mineru.model import MinerUStatus, MinerUTaskState
from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor
from rag.ingestion.parser.mineru.properties import MinerUProperties


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    def __init__(self, states):
        self._states = list(states)
        self.queries = 0

    async def query_result(self, batch_id):
        self.queries += 1
        if isinstance(self._states[0], Exception):
            raise self._states.pop(0)
        return self._states.pop(0)


def _executor(client, **props):
    return MinerUPollingExecutor(client, MinerUProperties(poll_interval_seconds=0.01, **props))


class TestSubmitAndAwait:
    def test_returns_when_done(self):
        client = _FakeClient([MinerUStatus("running", "", None), MinerUStatus("done", "http://z", None)])
        status = _run(_executor(client).submit_and_await("b1"))
        assert status.state == "done"
        assert client.queries == 2

    def test_raises_when_failed(self):
        client = _FakeClient([MinerUStatus("failed", "", "boom")])
        with pytest.raises(ServiceException) as ei:
            _run(_executor(client).submit_and_await("b1"))
        assert "boom" in str(ei.value)

    def test_raises_on_timeout(self):
        client = _FakeClient([MinerUStatus("running", "", None)] * 100)
        with pytest.raises(ServiceException) as ei:
            _run(_executor(client, timeout_seconds=1).submit_and_await("b1"))
        assert "超时" in str(ei.value)

    def test_transient_error_retries_until_done(self):
        client = _FakeClient(
            [ServiceException("net down"), MinerUStatus("running", "", None), MinerUStatus("done", "http://z", None)]
        )
        status = _run(_executor(client, timeout_seconds=5).submit_and_await("b1"))
        assert status.state == "done"
        assert client.queries == 3

    def test_transient_error_until_deadline_raises(self):
        client = _FakeClient([ServiceException("net down")] * 100)
        with pytest.raises(ServiceException) as ei:
            _run(_executor(client, timeout_seconds=1).submit_and_await("b1"))
        assert "持续失败" in str(ei.value)

    def test_blank_batch_id_raises(self):
        client = _FakeClient([])
        with pytest.raises(ServiceException):
            _run(_executor(client).submit_and_await("  "))
