"""MinerU 模型与枚举单测（对齐 Java MinerUTaskState / MinerUStatus / BatchSubmitRequest / BatchUploadTicket）"""
import pytest

from rag.ingestion.parser.mineru.model import (
    BatchSubmitRequest,
    BatchUploadTicket,
    MinerUStatus,
    MinerUTaskState,
)
from common.exception.business import ServiceException


class TestMinerUTaskState:
    def test_parse_known_states(self):
        assert MinerUTaskState.parse("pending") is MinerUTaskState.PENDING
        assert MinerUTaskState.parse("running") is MinerUTaskState.RUNNING
        assert MinerUTaskState.parse("done") is MinerUTaskState.DONE
        assert MinerUTaskState.parse("failed") is MinerUTaskState.FAILED

    def test_parse_case_insensitive(self):
        assert MinerUTaskState.parse("DONE") is MinerUTaskState.DONE

    def test_parse_unknown_raises(self):
        with pytest.raises(ServiceException):
            MinerUTaskState.parse("whatever")

    def test_parse_none_raises(self):
        with pytest.raises(ServiceException):
            MinerUTaskState.parse(None)


class TestMinerUStatus:
    def test_completed_when_done(self):
        assert MinerUStatus(MinerUTaskState.DONE, "http://z", None).completed()
        assert not MinerUStatus(MinerUTaskState.RUNNING, "http://z", None).completed()

    def test_failed_flag(self):
        assert MinerUStatus(MinerUTaskState.FAILED, "", "boom").failed()
        assert not MinerUStatus(MinerUTaskState.DONE, "", None).failed()

    def test_status_line(self):
        s = MinerUStatus(MinerUTaskState.RUNNING, "http://z", None)
        assert s.status_line() == "RUNNING"


class TestBatchSubmitRequest:
    def test_fields_default(self):
        r = BatchSubmitRequest("a.pdf", "doc-1", True, True, True, "ch")
        assert r.file_name == "a.pdf"
        assert r.data_id == "doc-1"
        assert r.is_ocr is True
        assert r.enable_table is True
        assert r.enable_formula is True
        assert r.language == "ch"


class TestBatchUploadTicket:
    def test_fields(self):
        t = BatchUploadTicket("b1", "http://u")
        assert t.batch_id == "b1"
        assert t.upload_url == "http://u"
