# P1 MinerU 外接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MinerU SaaS 文档解析服务外接到 Python 端，使 PDF/Word/PPT 及 FIDELITY 档 Excel 能通过「上传 → 轮询 → 下载 ZIP → 解包为 ParsedDocument」链路完成解析。

**Architecture:** 在 `rag/ingestion/parser/mineru/` 下新建独立子包，按 Java 参考实现 `core/parser/mineru/*` 一比一移植 7 个组件（properties/model/client/polling/unpacker/parser + `__init__`）。MinerU 解析是**异步**链路，故 `MinerUDocumentParser` 提供 `async_parse_structured` 主入口 + 同步 `parse_structured` 包装（对齐 ImageDocumentParser 双入口），并新增 kernel 异步分发（有 `async_parse_structured` 则 await）。结果 ZIP 解包复用 `markdown_parser` 的 token→Block 机制（给 `_extract_blocks` 增加可选 image 解析参数，DRY 不复制代码）。装配采用**条件注册**：仅当 `RAGENT_MINERU_API_KEY` 非空才把 MinerU 解析器加入 `ParserRegistry`（对齐 youcom 工具条件注册先例，无 key 时 PDF 上传仍按现状拒绝）。

**Tech Stack:** Python 3.13 / httpx（AsyncClient + MockTransport 测试）/ markdown-it-py（复用现有 markdown 解析）/ zipfile（标准库）/ asyncio（轮询与并发信号量）。无新增运行时第三方库；`requirements.txt` 补记 `markdown-it-py>=3.0`（历史欠账，MinerU 解包依赖它）。

---

## 背景与定位

- **上游参考**：`ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/core/parser/mineru/`（9 个类，本次移植其中 7 个 Python 对应物；`MinerUService`/`MinerUFileStorage` 属 bootstrap 装配层，由 wiring 承担）。
- **当前缺口**：`rag/ingestion/parser/pdf_parser.py` 是占位注释；`registry.py` 中 `SUPPORTED_EXTENSIONS` 注释明确「pdf/doc 等复杂格式待 MinerU 接入后追加」；`test_parser_registry_extension_unit.py` 断言 `not registry.can_parse("application/pdf")`（MinerU 未接入时 pdf 不可解析）。
- **配置来源**：`MinerUProperties.from_env()` 直接读 `RAGENT_MINERU_*` 环境变量，不新增 `app/config.py` 字段（对齐 P8 eval 开关的零运行时库侵入风格）。
- **关键架构决策**：
  - **kernel 异步分发（D1）**：`rag/ingestion/kernel.py` 解析节点目前同步调 `parser.parse_structured`，而 kernel 本身在 async 上下文执行 → 必须改为「有 `async_parse_structured` 则 await，否则走同步」，否则 MinerU 解析在真实链路必然抛「running loop」错误。
  - **条件注册（D2）**：`RAGENT_MINERU_API_KEY` 为空则不注册 MinerU，PDF/DOC/PPT 保持现状（上传前校验拒绝）；非空则注册，`SUPPORTED_EXTENSIONS` **本轮不扩展**（静态集，避免无 key 时 `scripts/ingest.py` 的 `self_check()` 失败）。
  - **并发限流（D3）**：Java 用 Redisson 分布式信号量；Python 单进程 MVP 用进程内 `asyncio.Semaphore` + `wait_for` 最大等待（对齐项目「Redisson 分布式锁降级为进程内」既有决策），无租约续期。
  - **图片描述可关（D4）**：解包器构造参数 `vlm_service` 可传 None，且 `ImageParseProperties` 新增 `embedded_describe_enabled`（对齐 Java `ImageParseProperties.isEmbeddedDescribeEnabled()`）。VLM 未接线时图片仍上传并生成 `ImageBlock`（有 URL），仅无描述。
  - **markdown 复用（D5）**：`markdown_parser._extract_blocks` 增加可选 `image_url_map` / `image_description_map` 参数（None 时行为逐字节不变），解包器直接复用，不复制 Block 转换逻辑。

## 目标文件清单

| 文件 | 责任 |
|---|---|
| Create: `rag/ingestion/parser/mineru/__init__.py` | 子包导出 |
| Create: `rag/ingestion/parser/mineru/properties.py` | `MinerUProperties`（env 驱动 + 默认值） |
| Create: `rag/ingestion/parser/mineru/model.py` | `MinerUTaskState`/`MinerUStatus`/`BatchSubmitRequest`/`BatchUploadTicket` |
| Create: `rag/ingestion/parser/mineru/client.py` | `MinerUClient`（requestUpload/uploadFile/queryResult/downloadZip，httpx.AsyncClient） |
| Create: `rag/ingestion/parser/mineru/polling.py` | `MinerUPollingExecutor`（异步轮询，超时/失败兜底） |
| Create: `rag/ingestion/parser/mineru/unpacker.py` | `MinerUResultUnpacker`（ZIP→Markdown→Blocks，图片上传+描述） |
| Create: `rag/ingestion/parser/mineru/parser.py` | `MinerUDocumentParser`（MIME 认领 + 双入口 parse 流程） |
| Modify: `rag/ingestion/parser/markdown_parser.py` | `_extract_blocks`/`_to_image_block`/`_extract_inline_text` 增加可选 image 解析参数（D5） |
| Modify: `rag/ingestion/parser/image_parser.py` | `ImageParseProperties` 新增 `embedded_describe_enabled`（D4） |
| Modify: `rag/ingestion/kernel.py` | 解析节点 async 分发（D1） |
| Modify: `app/wiring.py` | 条件装配 MinerU（D2），两处 ParserRegistry 均接入 |
| Modify: `scripts/ingest.py` | 条件注册 MinerU（离线入库 PDF 可解析） |
| Modify: `requirements.txt` | 补记 `markdown-it-py>=3.0`（历史欠账） |
| Create: `tests/test_mineru_model_unit.py` | 模型/枚举单测 |
| Create: `tests/test_mineru_properties_unit.py` | 配置单测 |
| Create: `tests/test_mineru_client_unit.py` | HTTP 客户端单测（MockTransport） |
| Create: `tests/test_mineru_polling_unit.py` | 轮询执行器单测 |
| Create: `tests/test_mineru_unpacker_unit.py` | 解包器单测 |
| Create: `tests/test_mineru_parser_unit.py` | 解析器单测 |
| Create: `tests/test_mineru_wiring_unit.py` | 条件接线单测 |
| Modify: `docs/ragent-file-by-file-comparison.md` | MinerU 行销案 + §12 P1 行 |
| Modify: `rag/README.md` | MinerU 能力说明 |
| Create: `docs/rag/mineru-guide.md` | 接入/配置/验证指南 |

## 验收标准

- [ ] 全量回归 563+（新增约 55 例）全绿，无失败（沙箱写保护 warning 不计）。
- [ ] 无 API key 时：`ParserRegistry` 不含 MinerU，`can_parse("application/pdf")` 为 False；有 key 时 True。
- [ ] `MinerUDocumentParser` 同步入口在无 loop 时经 `asyncio.run` 可完成全链路（fakes 注入），异步入口在 kernel 中被 await。
- [ ] 解包：ZIP 中首个 `.md` 解析为 Blocks；独立图片 → `ImageBlock`（URL 已解析 + 有描述）；正文内图片 URL 已改写为公网 URL；表格 → `TableBlock`/`HtmlTableBlock`；无 md 或空 ZIP 抛 `ServiceException`。
- [ ] 轮询：DONE 返回；FAILED 抛 `ServiceException`（含 err_msg）；超时抛 `ServiceException`；瞬时网络错误重试至 deadline。
- [ ] 对比文档 §12 P1 行与 MinerU 相关行销案；`rag/README.md` 与 `mineru-guide.md` 与代码一致。

---

### Task 1: MinerU 子包骨架 + 模型与配置（model.py / properties.py）

**Files:**
- Create: `rag/ingestion/parser/mineru/__init__.py`
- Create: `rag/ingestion/parser/mineru/model.py`
- Create: `rag/ingestion/parser/mineru/properties.py`
- Test: `tests/test_mineru_model_unit.py`, `tests/test_mineru_properties_unit.py`

- [ ] **Step 1: 写失败测试 `tests/test_mineru_model_unit.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_mineru_model_unit.py -q`
Expected: `ModuleNotFoundError: No module named 'rag.ingestion.parser.mineru'`

- [ ] **Step 3: 实现 `model.py`**

```python
"""
MinerU 数据模型（对应 ragent core/parser/mineru 的 MinerUTaskState / MinerUStatus / BatchSubmitRequest / BatchUploadTicket）

约定：MinerUStatus.state 统一归一化为小写字符串（"pending"/"running"/"done"/"failed"），
构造时接受 MinerUTaskState 或字符串，方便各层直接比较 status.state == "done"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from common.exception.business import ServiceException


class MinerUTaskState:
    """MinerU 任务状态枚举（字符串值对齐上游 API 返回值）"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: Optional[str]) -> "MinerUTaskState":
        """字符串 → 状态；未知或空值抛客户端异常（对齐 Java IllegalArgumentException 兜底）"""
        if value is None:
            raise ServiceException("MinerU 任务状态为空")
        lowered = str(value).strip().lower()
        for member in (cls.PENDING, cls.RUNNING, cls.DONE, cls.FAILED):
            if member == lowered:
                return cls._from_value(member)
        raise ServiceException(f"未知的 MinerU 任务状态: {value}")

    @staticmethod
    def _from_value(value: str) -> "MinerUTaskState":
        state = object.__new__(MinerUTaskState)
        state.value = value
        return state


@dataclass(frozen=True)
class MinerUStatus:
    """单次任务查询结果（对应 Java MinerUStatus）"""

    state: str
    zip_url: str
    error_message: Optional[str] = None

    def __init__(self, state, zip_url: str, error_message: Optional[str] = None):
        # state 兼容 MinerUTaskState 或字符串，统一归一化为小写字符串
        if isinstance(state, MinerUTaskState):
            state = state.value
        object.__setattr__(self, "state", str(state))
        object.__setattr__(self, "zip_url", zip_url)
        object.__setattr__(self, "error_message", error_message)

    def completed(self) -> bool:
        return self.state == MinerUTaskState.DONE

    def failed(self) -> bool:
        return self.state == MinerUTaskState.FAILED

    def status_line(self) -> str:
        return self.state.upper()


@dataclass(frozen=True)
class BatchSubmitRequest:
    """提交解析请求（对应 Java BatchSubmitRequest）"""

    file_name: str
    data_id: Optional[str] = None
    is_ocr: bool = False
    enable_table: bool = True
    enable_formula: bool = True
    language: Optional[str] = "ch"


@dataclass(frozen=True)
class BatchUploadTicket:
    """requestUpload 返回的预签名上传凭据（对应 Java BatchUploadTicket）"""

    batch_id: str
    upload_url: str
```

> 说明：`MinerUTaskState` 用轻量类而非 `enum.Enum`，贴近 Java 静态常量语义；如团队偏好 `enum.Enum` 可在本步替换，保持 `parse`/`value` 接口不变即可。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_mineru_model_unit.py -q`
Expected: `12 passed`

- [ ] **Step 5: 写失败测试 `tests/test_mineru_properties_unit.py`**

```python
"""MinerUProperties 单测：默认值与 env 覆盖"""
import pytest

from rag.ingestion.parser.mineru.properties import MinerUProperties


class TestMinerUPropertiesDefaults:
    def test_defaults(self):
        p = MinerUProperties()
        assert p.api_url == "https://mineru.net/api/v4"
        assert p.api_key == ""
        assert p.poll_interval_seconds == 10
        assert p.timeout_seconds == 1800
        assert p.max_wait_seconds == 30
        assert p.concurrency_limit == 2
        assert p.enable_table is True
        assert p.enable_formula is True
        assert p.ocr is False
        assert p.language == "ch"


class TestMinerUPropertiesFromEnv:
    def test_empty_env_uses_defaults(self, monkeypatch):
        for k in (
            "RAGENT_MINERU_API_URL",
            "RAGENT_MINERU_API_KEY",
            "RAGENT_MINERU_POLL_INTERVAL_SECONDS",
            "RAGENT_MINERU_TIMEOUT_SECONDS",
            "RAGENT_MINERU_MAX_WAIT_SECONDS",
            "RAGENT_MINERU_CONCURRENCY_LIMIT",
            "RAGENT_MINERU_ENABLE_TABLE",
            "RAGENT_MINERU_ENABLE_FORMULA",
            "RAGENT_MINERU_OCR",
            "RAGENT_MINERU_LANGUAGE",
        ):
            monkeypatch.delenv(k, raising=False)
        p = MinerUProperties.from_env()
        assert p.api_url == "https://mineru.net/api/v4"
        assert p.api_key == ""

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("RAGENT_MINERU_API_URL", "http://localhost:8080")
        monkeypatch.setenv("RAGENT_MINERU_API_KEY", "sk-test")
        monkeypatch.setenv("RAGENT_MINERU_POLL_INTERVAL_SECONDS", "2")
        monkeypatch.setenv("RAGENT_MINERU_TIMEOUT_SECONDS", "60")
        monkeypatch.setenv("RAGENT_MINERU_MAX_WAIT_SECONDS", "5")
        monkeypatch.setenv("RAGENT_MINERU_CONCURRENCY_LIMIT", "4")
        monkeypatch.setenv("RAGENT_MINERU_ENABLE_TABLE", "false")
        monkeypatch.setenv("RAGENT_MINERU_ENABLE_FORMULA", "false")
        monkeypatch.setenv("RAGENT_MINERU_OCR", "true")
        monkeypatch.setenv("RAGENT_MINERU_LANGUAGE", "en")
        p = MinerUProperties.from_env()
        assert p.api_url == "http://localhost:8080"
        assert p.api_key == "sk-test"
        assert p.poll_interval_seconds == 2
        assert p.timeout_seconds == 60
        assert p.max_wait_seconds == 5
        assert p.concurrency_limit == 4
        assert p.enable_table is False
        assert p.enable_formula is False
        assert p.ocr is True
        assert p.language == "en"

    def test_invalid_int_falls_back(self, monkeypatch):
        monkeypatch.setenv("RAGENT_MINERU_CONCURRENCY_LIMIT", "abc")
        p = MinerUProperties.from_env()
        assert p.concurrency_limit == 2
```

- [ ] **Step 6: 运行测试确认失败**

Run: `python -m pytest tests/test_mineru_properties_unit.py -q`
Expected: `ModuleNotFoundError: No module named 'rag.ingestion.parser.mineru.properties'`

- [ ] **Step 7: 实现 `properties.py` 与 `__init__.py`**

`rag/ingestion/parser/mineru/properties.py`:

```python
"""
MinerU 外部服务配置（对应 ragent MinerUProperties）

字段全部走 RAGENT_MINERU_* 环境变量，未配置时回落默认值；
api_key 为空时 wiring 层不注册 MinerU 解析器（条件装配，对齐 youcom 工具先例）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class MinerUProperties:
    api_url: str = field(default="https://mineru.net/api/v4")
    api_key: str = field(default="")
    poll_interval_seconds: int = field(default=10)
    timeout_seconds: int = field(default=1800)
    max_wait_seconds: int = field(default=30)
    concurrency_limit: int = field(default=2)
    enable_table: bool = field(default=True)
    enable_formula: bool = field(default=True)
    ocr: bool = field(default=False)
    language: str = field(default="ch")

    @classmethod
    def from_env(cls) -> "MinerUProperties":
        def _int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or raw.strip() == "":
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _bool(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            if raw is None or raw.strip() == "":
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        return cls(
            api_url=os.getenv("RAGENT_MINERU_API_URL", "https://mineru.net/api/v4"),
            api_key=os.getenv("RAGENT_MINERU_API_KEY", ""),
            poll_interval_seconds=_int("RAGENT_MINERU_POLL_INTERVAL_SECONDS", 10),
            timeout_seconds=_int("RAGENT_MINERU_TIMEOUT_SECONDS", 1800),
            max_wait_seconds=_int("RAGENT_MINERU_MAX_WAIT_SECONDS", 30),
            concurrency_limit=_int("RAGENT_MINERU_CONCURRENCY_LIMIT", 2),
            enable_table=_bool("RAGENT_MINERU_ENABLE_TABLE", True),
            enable_formula=_bool("RAGENT_MINERU_ENABLE_FORMULA", True),
            ocr=_bool("RAGENT_MINERU_OCR", False),
            language=os.getenv("RAGENT_MINERU_LANGUAGE", "ch"),
        )
```

`rag/ingestion/parser/mineru/__init__.py`:

```python
"""
rag.ingestion.parser.mineru - MinerU SaaS 文档解析外接子包

    - properties：MinerUProperties（env 驱动配置）
    - model：MinerUTaskState / MinerUStatus / BatchSubmitRequest / BatchUploadTicket
    - client：MinerUClient（requestUpload / uploadFile / queryResult / downloadZip）
    - polling：MinerUPollingExecutor（异步轮询执行器）
    - unpacker：MinerUResultUnpacker（ZIP → Markdown → Blocks）
    - parser：MinerUDocumentParser（MIME 认领 + 双入口 parse 流程）
"""
from rag.ingestion.parser.mineru.parser import MinerUDocumentParser

__all__ = ["MinerUDocumentParser"]
```

> 注：`__init__.py` 先只导出 `MinerUDocumentParser`，其余模块由各 Task 逐个补入。

- [ ] **Step 8: 运行测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_mineru_model_unit.py tests/test_mineru_properties_unit.py -q`
Expected: `15 passed`

Run: `python -m pytest -q 2>&1 | Select-Object -Last 3`
Expected: 基线 563 全绿，总数 +15。

- [ ] **Step 9: Commit**

```bash
git add rag/ingestion/parser/mineru tests/test_mineru_model_unit.py tests/test_mineru_properties_unit.py
git commit -m "feat(mineru): add mineru model & properties"
```

---

### Task 2: MinerU HTTP 客户端（client.py）

**Files:**
- Create: `rag/ingestion/parser/mineru/client.py`
- Modify: `rag/ingestion/parser/mineru/__init__.py`（补导出 `MinerUClient`）
- Test: `tests/test_mineru_client_unit.py`

- [ ] **Step 1: 写失败测试 `tests/test_mineru_client_unit.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_mineru_client_unit.py -q`
Expected: `ModuleNotFoundError: No module named 'rag.ingestion.parser.mineru.client'`

- [ ] **Step 3: 实现 `client.py`**

```python
"""
MinerU SaaS HTTP 客户端（对应 ragent MinerUClient）

四类请求：
    - requestUpload：POST {api_url}/file-urls/batch，申请预签名上传地址（需 Bearer api_key）
    - uploadFile：   PUT 预签名地址直传（无鉴权头、无 Content-Type，对齐上游）
    - queryResult：  GET {api_url}/extract-results/batch/{batchId}（需 Bearer api_key）
    - downloadZip：  GET 预签名 zip 地址（无鉴权头）

全部方法均为 async，内部共用注入的 httpx.AsyncClient（不阻塞事件循环，对齐项目下载器约定）。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from common.exception.business import ServiceException
from rag.ingestion.parser.mineru.model import (
    BatchSubmitRequest,
    BatchUploadTicket,
    MinerUStatus,
    MinerUTaskState,
)
from rag.ingestion.parser.mineru.properties import MinerUProperties

logger = logging.getLogger(__name__)


class MinerUClient:
    def __init__(self, properties: MinerUProperties, http_client: Optional[httpx.AsyncClient] = None):
        self._properties = properties
        self._http = http_client  # 测试注入 MockTransport；None 时惰性自建

    # ---- 私有工具 ----

    def _require_api_key(self) -> None:
        if not self._properties.api_key:
            raise ServiceException("MinerU api-key 未配置，无法发起 requestUpload/queryResult")

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=60)
        return self._http

    async def _request_json(
        self, method: str, url: str, *, json: Optional[dict] = None, auth: bool = False
    ) -> dict:
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {self._properties.api_key}"
        try:
            resp = await self._http_client().request(method, url, json=json, headers=headers)
        except httpx.HTTPError as e:
            raise ServiceException(f"MinerU HTTP 请求失败 {method} {url}: {e}") from e
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ServiceException(f"MinerU HTTP 非 2xx {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise ServiceException(f"MinerU 响应非 JSON: {resp.text[:200]}") from e

    def _ensure_success(self, root: dict, op: str) -> None:
        code = root.get("code")
        if code is not None and int(code) != 0:
            msg = root.get("msg") or root.get("message") or ""
            raise ServiceException(f"MinerU {op} 业务失败 code={code} msg={msg}")

    # ---- 对外接口 ----

    async def request_upload(self, request: BatchSubmitRequest) -> BatchUploadTicket:
        self._require_api_key()
        payload: dict = {
            "enable_formula": request.enable_formula,
            "enable_table": request.enable_table,
            "language": request.language or "ch",
            "files": [
                {
                    "name": request.file_name,
                    "is_ocr": request.is_ocr,
                }
            ],
        }
        if request.data_id:
            payload["files"][0]["data_id"] = request.data_id
        url = f"{self._properties.api_url}/file-urls/batch"
        root = await self._request_json("POST", url, json=payload, auth=True)
        self._ensure_success(root, "requestUpload")
        data = root.get("data") or {}
        batch_id = data.get("batch_id") or ""
        if not batch_id:
            raise ServiceException(f"MinerU requestUpload 响应缺少 batch_id: {root}")
        file_urls = data.get("file_urls") or []
        if not file_urls or not file_urls[0]:
            raise ServiceException(f"MinerU requestUpload 响应缺少 file_urls: {root}")
        return BatchUploadTicket(batch_id=batch_id, upload_url=file_urls[0])

    async def upload_file(self, upload_url: str, content: bytes) -> None:
        try:
            resp = await self._http_client().put(upload_url, content=content)
        except httpx.HTTPError as e:
            raise ServiceException(f"MinerU 上传文件失败: {e}") from e
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ServiceException(f"MinerU 上传文件非 2xx {resp.status_code}: {resp.text[:200]}")

    async def query_result(self, batch_id: str) -> MinerUStatus:
        self._require_api_key()
        url = f"{self._properties.api_url}/extract-results/batch/{batch_id}"
        root = await self._request_json("GET", url, auth=True)
        self._ensure_success(root, "queryResult")
        data = root.get("data") or {}
        results = data.get("extract_result") or []
        if not results:
            # 上游未返回结果视为仍在运行（对齐 Java：extractResult 为空 → RUNNING）
            return MinerUStatus(state="running", zip_url="", error_message=None)
        first = results[0]
        state = MinerUTaskState.parse(first.get("state"))
        return MinerUStatus(
            state=state,
            zip_url=first.get("full_zip_url") or "",
            error_message=first.get("err_msg"),
        )

    async def download_zip(self, zip_url: str) -> bytes:
        try:
            resp = await self._http_client().get(zip_url)
        except httpx.HTTPError as e:
            raise ServiceException(f"MinerU 下载 zip 失败: {e}") from e
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ServiceException(f"MinerU 下载 zip 非 2xx {resp.status_code}: {resp.text[:200]}")
        return resp.content
```

- [ ] **Step 4: 更新 `__init__.py` 导出**

在 `rag/ingestion/parser/mineru/__init__.py` 末尾追加：

```python
from rag.ingestion.parser.mineru.client import MinerUClient

__all__ = ["MinerUDocumentParser", "MinerUClient"]
```

- [ ] **Step 5: 运行测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_mineru_client_unit.py -q`
Expected: `15 passed`

Run: `python -m pytest -q 2>&1 | Select-Object -Last 3`
Expected: 全绿，总数 +15。

- [ ] **Step 6: Commit**

```bash
git add rag/ingestion/parser/mineru tests/test_mineru_client_unit.py
git commit -m "feat(mineru): add http client"
```

---

### Task 3: 异步轮询执行器（polling.py）

**Files:**
- Create: `rag/ingestion/parser/mineru/polling.py`
- Modify: `rag/ingestion/parser/mineru/__init__.py`（补导出 `MinerUPollingExecutor`）
- Test: `tests/test_mineru_polling_unit.py`

- [ ] **Step 1: 写失败测试 `tests/test_mineru_polling_unit.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_mineru_polling_unit.py -q`
Expected: `ModuleNotFoundError: No module named 'rag.ingestion.parser.mineru.polling'`

- [ ] **Step 3: 实现 `polling.py`**

```python
"""
MinerU 任务轮询执行器（对应 ragent MinerUPollingExecutor）

Java 用 ScheduledExecutorService 4 线程调度 + CompletableFuture；Python 侧用 asyncio 协程天然异步，
无需线程池即可支撑大批并发任务（不占用任何阻塞线程）。

语义对齐：
    - DONE  → 返回状态
    - FAILED → 抛 ServiceException（携带 err_msg）
    - 超时   → 抛 ServiceException（等待超时）
    - 瞬时网络错误 → 记日志并继续轮询至 deadline（单点抖动不误杀）
"""
from __future__ import annotations

import logging
import time

from common.exception.business import ServiceException
from rag.ingestion.parser.mineru.client import MinerUClient
from rag.ingestion.parser.mineru.model import MinerUStatus
from rag.ingestion.parser.mineru.properties import MinerUProperties

logger = logging.getLogger(__name__)


class MinerUPollingExecutor:
    def __init__(self, client: MinerUClient, properties: MinerUProperties):
        self._client = client
        self._properties = properties

    async def submit_and_await(self, batch_id: str) -> MinerUStatus:
        if not batch_id or not batch_id.strip():
            raise ServiceException("MinerU batchId 不能为空")
        timeout_seconds = max(1, self._properties.timeout_seconds)
        interval = max(0.1, self._properties.poll_interval_seconds)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                status = await self._client.query_result(batch_id)
            except Exception as e:  # 瞬时网络/解析错误：继续轮询至 deadline
                if time.monotonic() >= deadline:
                    raise ServiceException(f"MinerU 轮询持续失败到超时 batchId={batch_id}: {e}") from e
                logger.warning("MinerU 轮询瞬时错误，重试 batchId=%s: %s", batch_id, e)
                await asyncio_sleep(interval)
                continue
            if status.completed():
                return status
            if status.failed():
                raise ServiceException(
                    f"MinerU 任务失败 batchId={batch_id} err={status.error_message}"
                )
            if time.monotonic() >= deadline:
                raise ServiceException(f"MinerU 任务等待超时 batchId={batch_id}")
            await asyncio_sleep(interval)


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
```

> 说明：`asyncio_sleep` 用模块内封装便于测试直接驱动（也可直接 `import asyncio`，二选一即可；若直接 import，删除该 helper 并把两处调用改为 `await asyncio.sleep(interval)`）。

- [ ] **Step 4: 更新 `__init__.py` 导出**

```python
from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor

__all__ = ["MinerUDocumentParser", "MinerUClient", "MinerUPollingExecutor"]
```

- [ ] **Step 5: 运行测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_mineru_polling_unit.py -q`
Expected: `6 passed`

Run: `python -m pytest -q 2>&1 | Select-Object -Last 3`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add rag/ingestion/parser/mineru tests/test_mineru_polling_unit.py
git commit -m "feat(mineru): add async polling executor"
```

---

### Task 4: 结果解包器 + markdown 图片解析复用（unpacker.py / markdown_parser 改造）

**Files:**
- Create: `rag/ingestion/parser/mineru/unpacker.py`
- Modify: `rag/ingestion/parser/markdown_parser.py`（`_extract_blocks` / `_to_image_block` / `_extract_inline_text` 增加可选参数）
- Modify: `rag/ingestion/parser/image_parser.py`（`ImageParseProperties` 新增 `embedded_describe_enabled`）
- Modify: `rag/ingestion/parser/mineru/__init__.py`（补导出 `MinerUResultUnpacker`）
- Test: `tests/test_mineru_unpacker_unit.py`

> **前置读码**：先通读 `rag/ingestion/parser/markdown_parser.py`（约 200 行），确认 `_PARSER`、`_extract_blocks`、`_to_image_block`、`_extract_inline_text`、`_as_standalone_image` 现有实现，按下面契约改签名（全部可选参数，None 时行为逐字节不变）。

- [ ] **Step 1: 改造 `markdown_parser.py`（D5，向后兼容）**

三个函数签名与行为按以下契约修改（每处仅新增可选参数与「有 map 才走解析」分支）：

```python
def _extract_blocks(tokens, prov, image_url_map=None, image_description_map=None):
    ...
    # 原有 loop 体内，standalone 图片分支改为：
        standalone = _as_standalone_image(children)
        if standalone is not None:
            blocks.append(_to_image_block(standalone, prov, image_url_map, image_description_map))
            continue
        text = _extract_inline_text(children, image_url_map)
    ...


def _to_image_block(image, prov, image_url_map=None, image_description_map=None):
    raw_url = image.attrGet("src") or ""
    url = _resolve_image_url(raw_url, image_url_map)
    zip_path = _resolve_zip_path(raw_url, image_url_map)
    description = image_description_map.get(zip_path) if zip_path and image_description_map else None
    alt_text = image.content or ""
    return ImageBlock(prov, AssetRef(url, _guess_image_mime(url)), alt_text, alt_text, description)


def _extract_inline_text(children, image_url_map=None):
    ...
    # image 分支改为：
        elif ctype == "image":
            raw_url = c.attrGet("src") or ""
            url = _resolve_image_url(raw_url, image_url_map)
            alt = c.content or ""
            sb.append("![" + alt + "](" + url + ")")
    ...


def _resolve_image_url(raw_url, image_url_map):
    """MinerU zip 路径 → 公网 URL；无 map 或未命中时原样返回（对齐 Java resolveZipPath 语义）"""
    if not image_url_map:
        return raw_url
    if raw_url in image_url_map:
        return image_url_map[raw_url]
    stripped = raw_url
    if stripped.startswith("./"):
        stripped = stripped[2:]
    if stripped in image_url_map:
        return image_url_map[stripped]
    # 文件名级兜底（zip 内路径可能带子目录前缀）
    import os
    filename = os.path.basename(stripped.replace("\\", "/"))
    for zip_path, public_url in image_url_map.items():
        if os.path.basename(zip_path.replace("\\", "/")) == filename:
            return public_url
    return raw_url


def _resolve_zip_path(raw_url, image_url_map):
    """公网 URL 反查 zip 内路径（用于关联描述）；无命中返回 None"""
    if not image_url_map:
        return None
    if raw_url in image_url_map:
        return raw_url
    stripped = raw_url[2:] if raw_url.startswith("./") else raw_url
    if stripped in image_url_map:
        return stripped
    return None
```

> **验证**：改造后先跑全量回归，`markdown_parser` 现有消费方（分块 fixture 等）必须保持全绿（可选参数默认 None 即旧行为）。

- [ ] **Step 2: 写失败测试 `tests/test_mineru_unpacker_unit.py`**

```python
"""MinerUResultUnpacker 单测：ZIP 解包 → Blocks + 图片 URL 改写 + 描述注入"""
import asyncio
import io
import zipfile

import pytest

from common.exception.business import ServiceException
from rag.ingestion.parser.image_parser import ImageParseProperties
from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker
from rag.ingestion.parser.model import ImageBlock, TableBlock, HtmlTableBlock, ParserType


def _run(coro):
    return asyncio.run(coro)


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _FakeStorage:
    def __init__(self):
        self.calls = []

    def upload_asset(self, *, data, object_name, content_type=None):
        self.calls.append((object_name, content_type))
        return f"http://oss/{object_name}"

    def get_public_url(self, object_name):
        return f"http://oss/{object_name}"


class _FakeVlm:
    def __init__(self, descriptions):
        self._descriptions = descriptions
        self.calls = []

    async def describe_image(self, image_bytes, prompt=None, max_output_tokens=None):
        self.calls.append(len(image_bytes))
        return self._descriptions.get(len(image_bytes), "默认描述")


def _unpacker(storage=None, vlm=None, props=None):
    return MinerUResultUnpacker(
        storage or _FakeStorage(), vlm, props or ImageParseProperties()
    )


class TestUnpackMarkdown:
    def test_headings_and_paragraphs(self):
        md = "# 标题\n\n这是正文。\n"
        z = _zip_bytes({"a.md": md})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        assert parsed.metadata["parser"] == ParserType.MINERU.value
        texts = [b.text for b in parsed.blocks if hasattr(b, "text")]
        assert "标题" in texts
        assert "这是正文。" in texts

    def test_table_block(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
        z = _zip_bytes({"a.md": md})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        assert any(isinstance(b, TableBlock) for b in parsed.blocks)

    def test_html_table_block(self):
        md = '<table><tr><td>x</td></tr></table>\n'
        z = _zip_bytes({"a.md": md})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        assert any(isinstance(b, HtmlTableBlock) for b in parsed.blocks)

    def test_standalone_image_promoted_with_url_and_description(self):
        md = "![截图](images/1.png)\n"
        z = _zip_bytes({"a.md": md, "images/1.png": b"PNG"})
        vlm = _FakeVlm({3: "图表描述"})
        parsed = _run(_unpacker(_FakeStorage(), vlm).unpack(z, "a.pdf", "doc-1"))
        img = next(b for b in parsed.blocks if isinstance(b, ImageBlock))
        assert img.asset.public_url.startswith("http://oss/assets/doc-1/")
        assert img.asset.public_url.endswith(".png")
        assert img.description == "图表描述"
        assert parsed.metadata["imagesUploaded"] == 1
        assert parsed.metadata["imagesDescribed"] == 1

    def test_inline_image_url_rewritten(self):
        md = "看 ![图](images/a.png) 这里\n"
        z = _zip_bytes({"a.md": md, "images/a.png": b"PNG"})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        text = "\n".join(b.text for b in parsed.blocks)
        assert "http://oss/" in text
        assert "images/a.png" not in text

    def test_no_markdown_raises(self):
        z = _zip_bytes({"images/1.png": b"PNG"})
        with pytest.raises(ServiceException):
            _run(_unpacker().unpack(z, "a.pdf", "doc-1"))

    def test_empty_zip_bytes_raises(self):
        with pytest.raises(ServiceException):
            _run(_unpacker().unpack(b"", "a.pdf", "doc-1"))

    def test_embedded_describe_disabled_skips_vlm(self):
        md = "![截图](images/1.png)\n"
        z = _zip_bytes({"a.md": md, "images/1.png": b"PNG"})
        vlm = _FakeVlm({})
        props = ImageParseProperties(embedded_describe_enabled=False)
        parsed = _run(_unpacker(_FakeStorage(), vlm, props).unpack(z, "a.pdf", "doc-1"))
        assert vlm.calls == []
        assert parsed.metadata["imagesDescribed"] == 0

    def test_vlm_error_degrades_to_warning(self):
        class _BoomVlm:
            async def describe_image(self, *a, **k):
                raise RuntimeError("vlm down")

        md = "![截图](images/1.png)\n"
        z = _zip_bytes({"a.md": md, "images/1.png": b"PNG"})
        parsed = _run(_unpacker(_FakeStorage(), _BoomVlm()).unpack(z, "a.pdf", "doc-1"))
        assert parsed.metadata["imagesDescribed"] == 0
```

> 说明：`unpack` 是全异步链路（图片上传、VLM 描述均为 async），测试统一 `asyncio.run` 包裹。

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_mineru_unpacker_unit.py -q`
Expected: `ModuleNotFoundError: No module named 'rag.ingestion.parser.mineru.unpacker'`

- [ ] **Step 4: 扩展 `ImageParseProperties`（D4）**

在 `rag/ingestion/parser/image_parser.py` 的 `ImageParseProperties` dataclass 追加字段（放在现有字段之后，保持向后兼容）：

```python
@dataclass
class ImageParseProperties:
    description_prompt: str = field(default="请用一句中文描述这张图片的内容")
    max_output_tokens: int = field(default=64)
    embedded_describe_enabled: bool = field(default=True)  # 对齐 Java isEmbeddedDescribeEnabled()
```

> 若现有 `ImageParseProperties` 已定义 `description_prompt`/`max_output_tokens`，仅需在其后追加 `embedded_describe_enabled` 一行。

- [ ] **Step 5: 实现 `unpacker.py`**

```python
"""
MinerU 结果 ZIP 解包器（对应 ragent MinerUResultUnpacker）

链路：ZIP → 首个 .md + 图片字节集 → 图片上传对象存储（assets/{documentId}/...）
    → VLM 生成图片描述（可关）→ 复用 markdown_parser._extract_blocks 产出 Blocks
    （传入 image_url_map / image_description_map，独立图提升为 ImageBlock、正文内图 URL 改写）。
"""
from __future__ import annotations

import io
import logging
import uuid
import zipfile
from typing import Dict, Optional, Tuple

from common.exception.business import ServiceException
from rag.ingestion.parser.base import ParserType
from rag.ingestion.parser.image_parser import ImageParseProperties
from rag.ingestion.parser.markdown_parser import _PARSER, _extract_blocks
from rag.ingestion.parser.model import ParsedDocument, Provenance

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}


class MinerUResultUnpacker:
    def __init__(
        self,
        file_storage_service,
        vlm_service=None,
        properties: Optional[ImageParseProperties] = None,
    ):
        self._storage = file_storage_service
        self._vlm = vlm_service
        self._properties = properties or ImageParseProperties()

    async def unpack(self, zip_bytes: bytes, source_file: str, document_id: str) -> ParsedDocument:
        if not zip_bytes:
            raise ServiceException("MinerU 解包输入 ZIP 字节为空")
        markdown, images = self._read_zip(zip_bytes)
        if markdown is None:
            raise ServiceException("MinerU ZIP 中未找到 markdown 文件")
        image_url_map = await self._upload_images(images, document_id)
        image_description_map = await self._describe_images(images)
        prov = Provenance.of_file(source_file)
        tokens = _PARSER.parse(markdown)
        blocks = _extract_blocks(tokens, prov, image_url_map, image_description_map)
        return ParsedDocument.of(
            blocks,
            {
                "parser": ParserType.MINERU.value,
                "imagesUploaded": len(image_url_map),
                "imagesDescribed": len(image_description_map),
                "blocks": len(blocks),
            },
        )

    # ---- 私有 ----

    def _read_zip(self, zip_bytes: bytes) -> Tuple[Optional[str], Dict[str, bytes]]:
        images: Dict[str, bytes] = {}
        markdown: Optional[str] = None
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    norm = name.replace("\\", "/")
                    ext = norm.rsplit(".", 1)[-1].lower() if "." in norm else ""
                    if markdown is None and ext == "md":
                        markdown = zf.read(name).decode("utf-8", errors="replace")
                    elif ext in _IMAGE_EXTS:
                        images[name] = zf.read(name)
        except (zipfile.BadZipFile, OSError) as e:
            raise ServiceException(f"MinerU ZIP 读取失败: {e}") from e
        return markdown, images

    async def _upload_images(
        self, images: Dict[str, bytes], document_id: str
    ) -> Dict[str, str]:
        url_map: Dict[str, str] = {}
        for zip_path, data in images.items():
            ext = zip_path.rsplit(".", 1)[-1].lower()
            object_name = f"assets/{document_id}/{uuid.uuid4().hex}.{ext}"
            content_type = _IMAGE_MIME.get(ext, "application/octet-stream")
            try:
                self._storage.upload_asset(
                    data=data, object_name=object_name, content_type=content_type
                )
                url_map[zip_path] = self._storage.get_public_url(object_name)
            except Exception as e:  # 单张图片上传失败不中断整文档
                logger.warning("MinerU 图片上传失败 zip_path=%s: %s", zip_path, e)
        return url_map

    async def _describe_images(self, images: Dict[str, bytes]) -> Dict[str, str]:
        if self._vlm is None or not self._properties.embedded_describe_enabled:
            return {}
        description_map: Dict[str, str] = {}
        for zip_path, data in images.items():
            try:
                description = await self._vlm.describe_image(
                    data, prompt=self._properties.description_prompt,
                    max_output_tokens=self._properties.max_output_tokens,
                )
                if description:
                    description_map[zip_path] = description
            except Exception as e:  # 单张 VLM 失败降级为无描述
                logger.warning("MinerU 图片描述生成失败 zip_path=%s: %s", zip_path, e)
        return description_map
```

- [ ] **Step 6: 更新 `__init__.py` 导出**

```python
from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker

__all__ = ["MinerUDocumentParser", "MinerUClient", "MinerUPollingExecutor", "MinerUResultUnpacker"]
```

- [ ] **Step 7: 运行测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_mineru_unpacker_unit.py -q`
Expected: `11 passed`

Run: `python -m pytest -q 2>&1 | Select-Object -Last 3`
Expected: 全绿（含 markdown_parser 改造后的旧用例回归）。

- [ ] **Step 8: Commit**

```bash
git add rag/ingestion/parser tests/test_mineru_unpacker_unit.py
git commit -m "feat(mineru): add zip unpacker with image upload & describe"
```

---

### Task 5: MinerUDocumentParser + kernel 异步分发（parser.py / kernel.py）

**Files:**
- Create: `rag/ingestion/parser/mineru/parser.py`
- Modify: `rag/ingestion/kernel.py`（解析节点 async 分发）
- Modify: `rag/ingestion/parser/mineru/__init__.py`（确认导出）
- Test: `tests/test_mineru_parser_unit.py`, `tests/test_kernel_async_dispatch_unit.py`

- [ ] **Step 1: 先读 `rag/ingestion/kernel.py` 解析节点上下文**

Run: `python -c "import rag.ingestion.kernel as k; import inspect; print(inspect.getsource(k))" | Select-String -Pattern "parse_structured" -Context 3,3`
Expected: 找到解析节点调用 `parser.parse_structured(...)` 的位置（约 L289-291，位于 `async def run` 内）。

- [ ] **Step 2: 改造 `kernel.py` 解析节点（D1，向后兼容）**

把解析节点内对 `parse_structured` 的同步调用改为：

```python
parser = self._parser_registry.require(mime_type, effective_spec.parse_profile)
if hasattr(parser, "async_parse_structured"):
    parsed = await parser.async_parse_structured(content, mime_type, parser_options)
else:
    parsed = parser.parse_structured(content, mime_type, parser_options)
```

> 保持其余上下文（`mime_type` / `effective_spec` / `parser_options` 变量名以读到的实际代码为准，只改调用分发）。Text/Markdown 解析器无 `async_parse_structured` → 走原同步路径，行为不变。

- [ ] **Step 3: 写失败测试 `tests/test_kernel_async_dispatch_unit.py`**

```python
"""kernel 解析节点异步分发单测：async 解析器被 await，同步解析器走原路径"""
import asyncio
import pytest

from rag.ingestion.kernel import DefaultIngestionKernel
from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.model import ParsedDocument


def _run(coro):
    return asyncio.run(coro)


class _SyncParser(DocumentParser):
    @property
    def parser_type(self):
        return "sync"

    def supported_mime_types(self):
        return {ParseProfile.FAST: {"text/x-sync"}}

    def parse_structured(self, content, mime_type=None, options=None):
        return ParsedDocument.of([], {"parser": "sync"})


class _AsyncParser(DocumentParser):
    @property
    def parser_type(self):
        return ParserType.MINERU.value

    def supported_mime_types(self):
        return {ParseProfile.FAST: {"application/pdf"}}

    def parse_structured(self, content, mime_type=None, options=None):
        # 同步入口：有运行 loop 时必须抛错（对齐 MinerU 双入口），异步链路应走 async 入口
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_parse_structured(content, mime_type, options))
        raise RuntimeError("sync entry called inside running loop")

    async def async_parse_structured(self, content, mime_type=None, options=None):
        return ParsedDocument.of([], {"parser": ParserType.MINERU.value})


def _build_kernel(parsers):
    from rag.ingestion.parser.registry import ParserRegistry

    return DefaultIngestionKernel(parser_registry=ParserRegistry(parsers), ...)


def test_async_parser_dispatched_via_async_entry():
    # 构造最小 kernel（用真实依赖），喂一个 async 解析器跑完 run()，断言 metadata.parser 为 mineru
    ...
    parsed = _run(kernel.run(...))
    assert parsed.metadata["parser"] == ParserType.MINERU.value
```

> **注意**：`DefaultIngestionKernel` 构造函数字段较多（codec/schema/切分/向量化/入库等），本测试**只验证解析分发**——优先复用项目中已有 kernel 装配的 fixture/helper 构造最小 kernel；若无现成 helper，改为测试 kernel 内「分发逻辑」抽出的纯函数。落地时以实际 kernel 构造签名为准，保持「async 解析器被 await、sync 解析器被同步调用」两条断言成立。

- [ ] **Step 4: 运行测试确认失败**

Run: `python -m pytest tests/test_kernel_async_dispatch_unit.py -q`
Expected: 失败（kernel 尚未分发 async）。

- [ ] **Step 5: 写失败测试 `tests/test_mineru_parser_unit.py`**

```python
"""MinerUDocumentParser 单测（fakes 注入全链路）"""
import asyncio

import pytest

from common.exception.business import ServiceException
from rag.ingestion.parser.base import ParseProfile, ParserType
from rag.ingestion.parser.mineru.parser import MinerUDocumentParser
from rag.ingestion.parser.mineru.model import MinerUStatus
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
        with pytest.raises(RuntimeError):
            _run(asyncio.wrap_future(asyncio.ensure_future(_run_sync_in_loop(p))))

    def test_sync_entry_works_without_loop(self):
        # 无运行 loop：asyncio.run 包装走通
        p, client, polling, _ = _parser()
        parsed = p.parse_structured(
            b"PDF", "application/pdf", {"sourceFile": "a.pdf", "documentId": "doc-1"}
        )
        assert parsed.metadata["minerU.batchId"] == "b1"


async def _run_sync_in_loop(p):
    # 在 running loop 内调用同步入口应抛 RuntimeError
    p.parse_structured(b"PDF", "application/pdf", {})
    return None
```

> 说明：`test_sync_entry_raises_inside_running_loop` 为可选项；若断言复杂可删除，仅保留 `test_sync_entry_works_without_loop` 覆盖同步入口。

- [ ] **Step 6: 运行测试确认失败**

Run: `python -m pytest tests/test_mineru_parser_unit.py -q`
Expected: `ModuleNotFoundError: No module named 'rag.ingestion.parser.mineru.parser'`

- [ ] **Step 7: 实现 `parser.py`**

```python
"""
MinerU 文档解析器（对应 ragent MinerUDocumentParser）

认领（对齐 Java）：
    - FAST：PDF / Word / PPT 等布局型文档（这些格式仅有 MinerU 一条路径）
    - FIDELITY：Excel（FIDELITY 档优先 MinerU，未命中再回落 FAST 的 openpyxl 解析器）

双入口：
    - parse_structured（同步）：无运行 loop 时 asyncio.run 包装；有运行 loop 抛错引导用 async 入口
    - async_parse_structured（异步）：kernel 分发主路径

流程：requestUpload → uploadFile → 轮询 DONE → downloadZip → unpack → 合并 metadata。
并发限流：进程内 asyncio.Semaphore（单进程 MVP，对齐 Redisson 分布式信号量降级决策），
    wait_for 控制「获取许可最大等待时间」。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict, List, Optional, Set

from common.exception.business import ServiceException
from rag.ingestion.parser.base import DocumentParser, ParseProfile
from rag.ingestion.parser.mineru.client import MinerUClient
from rag.ingestion.parser.mineru.model import BatchSubmitRequest
from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor
from rag.ingestion.parser.mineru.properties import MinerUProperties
from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker
from rag.ingestion.parser.model import ParsedDocument, ParserType

logger = logging.getLogger(__name__)


class MinerUDocumentParser(DocumentParser):
    OPT_SOURCE_FILE = "sourceFile"
    OPT_DOCUMENT_ID = "documentId"
    META_BATCH_ID = "minerU.batchId"
    META_ZIP_URL = "minerU.zipUrl"

    # 布局型文档（FAST 档）：仅 MinerU 认领
    LAYOUT_MIME_TYPES: Set[str] = {
        "application/pdf",
        "application/x-pdf",
        "application/msword",
        "application/vnd.ms-word",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    }
    # 表格型文档（FIDELITY 档）：FIDELITY 优先 MinerU，FAST 回落 openpyxl
    SPREADSHEET_MIME_TYPES: Set[str] = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }

    def __init__(
        self,
        client: MinerUClient,
        polling_executor: MinerUPollingExecutor,
        result_unpacker: MinerUResultUnpacker,
        properties: MinerUProperties,
        semaphore: Optional[asyncio.Semaphore] = None,
    ):
        self._client = client
        self._polling = polling_executor
        self._unpacker = result_unpacker
        self._properties = properties
        self._semaphore = semaphore or asyncio.Semaphore(max(1, properties.concurrency_limit))

    @property
    def parser_type(self) -> str:
        return ParserType.MINERU.value

    def supported_mime_types(self) -> Dict[ParseProfile, Set[str]]:
        return {
            ParseProfile.FAST: self.LAYOUT_MIME_TYPES,
            ParseProfile.FIDELITY: self.SPREADSHEET_MIME_TYPES,
        }

    # ---- 同步入口（对齐 ImageDocumentParser 双入口）----

    def parse_structured(self, content: bytes, mime_type: Optional[str] = None, options: Optional[dict] = None) -> ParsedDocument:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_parse_structured(content, mime_type, options))
        raise RuntimeError(
            "MinerUDocumentParser 同步 parse_structured 不能在运行中的 event loop 内调用，"
            "请使用 async_parse_structured"
        )

    # ---- 异步主路径 ----

    async def async_parse_structured(
        self, content: bytes, mime_type: Optional[str] = None, options: Optional[dict] = None
    ) -> ParsedDocument:
        if not content:
            raise ServiceException("MinerU 解析输入字节为空")
        source_file = self._extract(options, self.OPT_SOURCE_FILE, "")
        document_id = self._extract(options, self.OPT_DOCUMENT_ID, uuid.uuid4().hex)
        upload_name = self._resolve_upload_name(source_file, mime_type, document_id)

        request = BatchSubmitRequest(
            file_name=upload_name,
            data_id=document_id,
            is_ocr=self._properties.ocr,
            enable_table=self._properties.enable_table,
            enable_formula=self._properties.enable_formula,
            language=self._properties.language,
        )

        # 获取信号量许可（最长等待 max_wait_seconds）
        max_wait = max(1, self._properties.max_wait_seconds)
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=max_wait)
        except asyncio.TimeoutError as e:
            raise ServiceException("MinerU 解析任务过多，请稍后重试") from e
        try:
            ticket = await self._client.request_upload(request)
            await self._client.upload_file(ticket.upload_url, content)
            status = await self._polling.submit_and_await(ticket.batch_id)
            zip_bytes = await self._client.download_zip(status.zip_url)
        finally:
            self._semaphore.release()

        parsed = await self._unpacker.unpack(zip_bytes, source_file, document_id)
        merged = dict(parsed.metadata or {})
        merged.update(
            {
                self.META_BATCH_ID: ticket.batch_id,
                self.META_ZIP_URL: status.zip_url,
                "parser": self.parser_type,
                "mimeType": mime_type or "",
            }
        )
        return ParsedDocument.of(parsed.blocks, merged)

    # ---- 私有 ----

    @staticmethod
    def _extract(options: Optional[dict], key: str, default: str) -> str:
        if not options:
            return default
        value = options.get(key)
        return value if value else default

    def _resolve_upload_name(self, source_file: str, mime_type: Optional[str], document_id: str) -> str:
        """上传文件名：优先 sourceFile，其次按 mime 推断扩展名（对齐 Java extractString）"""
        if source_file:
            return source_file
        ext = self._ext_from_mime(mime_type)
        return f"{document_id}{ext}"

    @staticmethod
    def _ext_from_mime(mime_type: Optional[str]) -> str:
        mapping = {
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.ms-powerpoint": ".ppt",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.slideshow": ".ppsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/vnd.ms-excel": ".xls",
        }
        return mapping.get(mime_type or "", "")
```

- [ ] **Step 8: 更新 `__init__.py`（确认已导出）**

`rag/ingestion/parser/mineru/__init__.py` 已含 `MinerUDocumentParser` 导出，无需改动（如无则补一行 `from rag.ingestion.parser.mineru.parser import MinerUDocumentParser`）。

- [ ] **Step 9: 运行测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_mineru_parser_unit.py tests/test_kernel_async_dispatch_unit.py -q`
Expected: 全绿

Run: `python -m pytest -q 2>&1 | Select-Object -Last 3`
Expected: 全绿（kernel 改造后旧链路回归）。

- [ ] **Step 10: Commit**

```bash
git add rag/ingestion tests/test_mineru_parser_unit.py tests/test_kernel_async_dispatch_unit.py
git commit -m "feat(mineru): add document parser & kernel async dispatch"
```

---

### Task 6: 条件装配（wiring.py / scripts/ingest.py）+ requirements

**Files:**
- Modify: `app/wiring.py`（`_build_mineru_parser` helper + 两处 ParserRegistry 条件追加）
- Modify: `scripts/ingest.py`（条件注册 MinerU）
- Modify: `requirements.txt`（补记 `markdown-it-py>=3.0`）
- Test: `tests/test_mineru_wiring_unit.py`

- [ ] **Step 1: 写失败测试 `tests/test_mineru_wiring_unit.py`**

```python
"""MinerU 条件接线单测：无 key 不注册 / 有 key 注册并认领 pdf"""
import pytest

from rag.ingestion.parser.base import ParseProfile
from rag.ingestion.parser.mineru.parser import MinerUDocumentParser
from rag.ingestion.parser.registry import ParserRegistry


class _FakeStorage:
    def upload_asset(self, **kwargs):
        return "http://oss/x"

    def get_public_url(self, object_name):
        return f"http://oss/{object_name}"


def _build_registry(with_key: bool, monkeypatch):
    if with_key:
        monkeypatch.setenv("RAGENT_MINERU_API_KEY", "sk-test")
    else:
        monkeypatch.delenv("RAGENT_MINERU_API_KEY", raising=False)
    from app.wiring import build_parser_registry  # 由本计划 Task6 引入的纯函数

    return build_parser_registry(_FakeStorage())


class TestWiring:
    def test_without_key_skips_mineru(self, monkeypatch):
        registry = _build_registry(False, monkeypatch)
        assert not registry.can_parse("application/pdf")

    def test_with_key_registers_mineru(self, monkeypatch):
        registry = _build_registry(True, monkeypatch)
        assert registry.can_parse("application/pdf")

    def test_mineru_instance_type(self, monkeypatch):
        registry = _build_registry(True, monkeypatch)
        parser = registry.require("application/pdf", ParseProfile.FAST)
        assert isinstance(parser, MinerUDocumentParser)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_mineru_wiring_unit.py -q`
Expected: `ModuleNotFoundError: No module named 'app.wiring'`（`build_parser_registry` 尚不存在）

- [ ] **Step 3: 在 `app/wiring.py` 增加纯函数 + 条件装配**

在 `app/wiring.py` 顶部 import 区追加：

```python
from rag.ingestion.parser.image_parser import ImageParseProperties
from rag.ingestion.parser.mineru.client import MinerUClient
from rag.ingestion.parser.mineru.parser import MinerUDocumentParser
from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor
from rag.ingestion.parser.mineru.properties import MinerUProperties
from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker
```

新增模块级纯函数（便于测试直调）：

```python
def build_parser_registry(file_storage, vlm_service=None):
    """构造 ParserRegistry：MinerU 仅在配置了 RAGENT_MINERU_API_KEY 时条件注册。

    对齐 youcom 工具条件注册先例：无 key 时 PDF/DOC/PPT 保持现状（上传前校验拒绝）。
    SUPPORTED_EXTENSIONS 本轮不扩展（静态集），避免无 key 环境 scripts/ingest.py 自检失败。
    """
    from rag.ingestion.parser.markdown_parser import MarkdownDocumentParser
    from rag.ingestion.parser.registry import ParserRegistry
    from rag.ingestion.parser.text_parser import TextDocumentParser

    parsers = [TextDocumentParser(), MarkdownDocumentParser()]
    mineru_props = MinerUProperties.from_env()
    if mineru_props.api_key:
        client = MinerUClient(mineru_props)
        polling = MinerUPollingExecutor(client, mineru_props)
        unpacker = MinerUResultUnpacker(file_storage, vlm_service, ImageParseProperties())
        parsers.append(MinerUDocumentParser(client, polling, unpacker, mineru_props))
    return ParserRegistry(parsers)
```

把 `app/wiring.py` 内两处 `ParserRegistry([TextDocumentParser(), MarkdownDocumentParser()])` 替换为调用 `build_parser_registry(file_storage)`（两处若已有 `file_storage` 局部变量直接传入；若该作用域无 `file_storage`，从 wiring 对应字段取 `self._file_storage` 或 `_get_shared_file_storage()`）。VLM 服务当前 wiring 未装配 → 传 `None`（图片仅上传 URL 无描述，D4）。

- [ ] **Step 4: `scripts/ingest.py` 条件注册**

将 ingest.py 中构造 `ParserRegistry` 的位置改为（复用同一 `build_parser_registry`，从 `app.wiring` 导入；若 ingest.py 已自建 parser 列表，则追加同样条件分支）：

```python
from app.wiring import build_parser_registry
from rag.ingestion.parser.registry import ParserRegistry
from rag.ingestion.parser.text_parser import TextDocumentParser
from rag.ingestion.parser.markdown_parser import MarkdownDocumentParser

# ...原有 parser 列表构造处，追加：
parsers = [TextDocumentParser(), MarkdownDocumentParser()]
# 条件注册 MinerU（无 key 时跳过）
from rag.ingestion.parser.mineru.properties import MinerUProperties
if MinerUProperties.from_env().api_key:
    from rag.ingestion.parser.mineru.client import MinerUClient
    from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor
    from rag.ingestion.parser.mineru.parser import MinerUDocumentParser
    from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker
    from rag.ingestion.parser.image_parser import ImageParseProperties
    props = MinerUProperties.from_env()
    client = MinerUClient(props)
    polling = MinerUPollingExecutor(client, props)
    unpacker = MinerUResultUnpacker(_build_file_storage(), None, ImageParseProperties())
    parsers.append(MinerUDocumentParser(client, polling, unpacker, props))
registry = ParserRegistry(parsers)
```

> 说明：ingest.py 的 `_build_file_storage()` 以其现有本地存储装配为准（无则用内存实现 `InMemoryFileStorage` 或上传到本地的 storage service）。落地时以 ingest.py 现有 `FileStorageService` 构建代码为准。

- [ ] **Step 5: `requirements.txt` 补记**

在 `requirements.txt` 追加一行（markdown-it-py 已被 markdown_parser 使用，属历史欠账补记；MinerU 解包同样依赖）：

```
markdown-it-py>=3.0
```

- [ ] **Step 6: 运行测试确认通过 + 全量回归**

Run: `python -m pytest tests/test_mineru_wiring_unit.py -q`
Expected: `3 passed`

Run: `python -m pytest -q 2>&1 | Select-Object -Last 3`
Expected: 全绿。

Run: `python scripts/ingest.py --help 2>&1 | Select-Object -First 3`
Expected: CLI 正常打印 usage（MinerU 条件注册不破坏离线入库）。

- [ ] **Step 7: Commit**

```bash
git add app/wiring.py scripts/ingest.py requirements.txt tests/test_mineru_wiring_unit.py
git commit -m "feat(mineru): conditional wiring & ingest registration"
```

---

### Task 7: 文档 + 收官（mineru-guide.md / README / 对比文档销案）

**Files:**
- Create: `docs/rag/mineru-guide.md`
- Modify: `rag/README.md`
- Modify: `docs/ragent-file-by-file-comparison.md`
- Modify: `docs/complements/p1-mineru-integration-implementation-plan.md`（§7 收官记录）

- [ ] **Step 1: 写 `docs/rag/mineru-guide.md`**

覆盖：启用前置（注册 MinerU 账号获取 API Key）、环境变量清单（`RAGENT_MINERU_API_KEY` / `_API_URL` / `_POLL_INTERVAL_SECONDS` / `_TIMEOUT_SECONDS` / `_MAX_WAIT_SECONDS` / `_CONCURRENCY_LIMIT` / `_ENABLE_TABLE` / `_ENABLE_FORMULA` / `_OCR` / `_LANGUAGE`）、无 key 行为（PDF 上传被拒）、支持格式表（FAST: pdf/word/ppt；FIDELITY: xlsx/xls）、图片描述开关（`embedded_describe_enabled` 与 VLM 未接线时的降级）、验证方式（`scripts/ingest.py --file a.pdf ...`）、已知限制（VLM 未接线无描述；进程内信号量单进程；`SUPPORTED_EXTENSIONS` 未含 pdf）。

- [ ] **Step 2: 更新 `rag/README.md`**

在解析器能力清单处追加：`PDF/Word/PPT（MinerU 外接，需 RAGENT_MINERU_API_KEY，可选）`；并在「外部依赖」或「配置」章节补充 `RAGENT_MINERU_*` 环境变量说明，链接 `docs/rag/mineru-guide.md`。

- [ ] **Step 3: 更新 `docs/ragent-file-by-file-comparison.md`**

- 在对比表中，MinerU 相关类（MinerUClient/MinerUPollingExecutor/MinerUResultUnpacker/MinerUDocumentParser/MinerUProperties 等）标记为「已移植」或消差；
- §12 总表 P1 行：把「MinerU 外接」从未实现/待办改为 ✅，并注明新增测试数与全量回归基线。

- [ ] **Step 4: 全量回归 + 性能烟雾**

Run: `python -m pytest -q 2>&1 | Select-Object -Last 3`
Expected: 全绿（基线 563 + 新增约 55 ≈ 618）

Run: `python -m pytest tests/test_mineru_client_unit.py tests/test_mineru_polling_unit.py tests/test_mineru_unpacker_unit.py tests/test_mineru_parser_unit.py tests/test_mineru_wiring_unit.py -q`
Expected: 全部通过（新增用例复跑确认）。

- [ ] **Step 5: 本计划 §7 收官记录**

在本文件末尾追加收官记录：里程碑全部 ✅、出口测试数、已知限制（VLM 未接线 / 进程内信号量 / SUPPORTED_EXTENSIONS 未扩展）、后续候选（P2 框架尾款 / P6 real 栈 / MinerU 真实 API 联调）。

- [ ] **Step 6: Commit**

```bash
git add docs/rag/mineru-guide.md rag/README.md docs/ragent-file-by-file-comparison.md docs/complements/p1-mineru-integration-implementation-plan.md
git commit -m "docs(mineru): guide, readme, comparison close-out"
```

---

## 风险与已知限制

| 项 | 说明 |
|---|---|
| VLM 未接线 | wiring 当前无 VLM 服务，MinerU 图片仅上传 URL 无描述；`embedded_describe_enabled` 已就位，后续接 VLM 即生效 |
| 进程内信号量 | 对齐项目「Redisson 分布式锁降级为进程内」决策，无跨实例租约；单实例下 `concurrency_limit` 生效 |
| `SUPPORTED_EXTENSIONS` 未扩展 | 静态集不纳入 pdf/doc/ppt，避免无 key 环境自检失败；对比文档按此销案 |
| markdown-it-py 依赖补记 | 已实际使用但未声明，本轮补记到 requirements.txt，非新增安装 |
| 真实 API 联调 | 本机无 MinerU 账号/无网络联调，成功路径由 MockTransport + fakes 锁定；真实联调列入后续 P6 real 栈 |

## 7. 收官记录

> 执行于 2026-08-23，本计划 Task 1-7 全部完成（✅）。

### 完成口径（重要）

P1 MinerU 外接按**「开发完成 / 代码级收官」**销案，**不标记「生产联调完成」**：

| 类别 | 项 | 状态 |
|---|---|---|
| ✅ 已完成 | MinerU 外接工程实现（requestUpload→uploadFile→轮询 DONE→downloadZip→解包） | 开发完成 |
| ✅ 已完成 | 离线契约、失败路径与 Mock 验收（MockTransport + fakes，57 例单测） | 开发完成 |
| ✅ 已完成 | 条件装配（无 key 不注册，PDF/Word/PPT 保持不可解析） | 开发完成 |
| ⏳ 延期 | 真实 MinerU API 联调 | 挂后续 real 栈阶段（P6） |
| ⏳ 延期 | VLM 图片描述生产接线 | 挂后续 real 栈阶段（P6） |

**部署限制**：
- `asyncio.Semaphore` 并发限流**仅适合单实例**；多实例部署需分布式信号量或租约（对齐 Redisson 降级决策）。
- `SUPPORTED_EXTENSIONS` 未纳入 pdf/doc/ppt 是合理的条件装配策略；**无 key 环境下文档与 UI 不得宣称支持这些格式**（上传前校验拒绝）。

- **Task 1-7 全部 ✅**：properties/model/client/polling/unpacker/parser + `__init__.py` 懒加载导出、kernel 解析节点 async 分发、markdown_parser 图片 URL 改写 + 描述注入、image_parser `embedded_describe_enabled`、registry `_EXTENSION_TO_MIME` 补 `pdf`、wiring/ingest 条件装配、requirements.txt 补记、文档收官（guide/README/对比文档销案）。
- **出口测试**：全量回归 **620 passed**（基线 563 + 新增 **57** 例 MinerU 测试，覆盖 model/properties/client/polling/unpacker/parser/wiring/kernel）。
- **已知限制**（详见上文「风险与已知限制」）：
  1. VLM 未接线，图片仅上传 URL 无描述（`embedded_describe_enabled` 已就位，后续接 VLM 即生效）；
  2. 进程内 asyncio.Semaphore 限流，单实例生效（对齐 Redisson 降级决策）；
  3. `SUPPORTED_EXTENSIONS` 静态集未纳入 pdf/doc/ppt（避免无 key 环境自检失败）；
  4. markdown-it-py 依赖补记，历史欠账非新增安装；
  5. 真实 MinerU API 联调未做，成功路径由 MockTransport + fakes 锁定。
- **后续候选**：P2 框架尾款（消费幂等/专用配置校验器/日志脱敏）、P6 real 栈（真实服务联调）、MinerU 真实 API 联调。
- **交付文档**：`docs/rag/mineru-guide.md`（启用前置/环境变量/支持格式/验证方式/已知限制）、`rag/README.md`（能力与环境变量）、`docs/ragent-file-by-file-comparison.md`（MinerU 类表与 §12 P1 销案）。
