# -*- coding: utf-8 -*-
"""P10.1 T1.9 integration：观测管线四态端到端 + unknown=0 审计 + 可测承诺 2/3/4/9/10 核对

四态（P10.1 DoD：流式/非流式/失败/canceled 各一条明细）走真实组件：
RoutingLLMService（真实 RoutingExecutor + 真实熔断器状态机）→ 观测发射器 →
ObservationHub（真实有界队列/双阈值 flush 管线）→ LlmCallDao → 行断言——
另组合断言 T1.8 固化钩子不变量（pricing_snapshot 始终写 + cost_usd 恒空）。
同域细粒度语义见 T1.3/T1.4 单测（test_observation_chat_emit_unit /
test_observation_stream_emit_unit），本文件管集成层的组合正确性。

不设 env 门控：conftest 的 D7 门控面向真实后端依赖（PG/Redis/MinIO），本文件
纯内存即可跑通整条观测管线（无外部副作用），须随全量回归执行。

可测承诺核对索引（证据 = 既有已提交测试 + 本文件核对；全量回归绿 = 承诺成立）：
    2  停机 drain 不丢已入队（kill -9 除外）→ test_observation_flush_unit
       （trace_unit 内 emit→drain 端到端 + lifespan 停机序 test_lifespan_wires_hub_and_drains_on_exit）
    3  双阈值 200 行/1s + put_nowait 非阻塞 → test_observation_queue_unit
    4  丢弃计数进 /metrics → test_observation_flush_unit.test_metrics_smoke
       （超阈值 P2 告警面挂 T4.4，P10.1 内只核对指标可见）
    9  observer 回调异常吞掉 + 计数 → test_observation_breaker_unit
    10 health_store 存量签名零改动 → T1.9 实测审计：68ce395（P9 收尾基线）的
       __init__(failure_threshold, open_duration_ms) / _now_ms / _next_token /
       is_unavailable / allow_call / mark_success / mark_failure /
       release_half_open_permit 逐一比对 HEAD，仅新增 add_state_observer /
       _notify_transition / snapshot_states，存量零签名变动（实测数据，非推断）
    （承诺 5/1/6/7/8/12 的验收分别挂 T1.8 指标面 / T2.3 / T4.3 / T3.3+T4.4 / T5 /
     T3.2——P10.1 内核对可见性即可，T1.9 不越权替跑）

unknown=0（D5 观测层自健康指标，DoD「调用点全覆盖验证」）：
    动态面 = 四条 E2E 行的 scene/purpose 无 unknown；
    静态面 = test_static_audit_call_sites_declare_scene_and_purpose——运行时
    llm_call_scope 调用点必须显式 scene=/purpose=、set_call_scope 仅允许
    trace_runner（overlay 继承形态）、调用点数下界 15 防调用点增删静默漂移。
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from core.llm.chat import RoutingLLMService
from core.llm.callback import BaseStreamCallback
from core.llm.config.config import ModelCandidate, ProviderConfig
from core.llm.enums import Tier
from core.llm.model.health_store import ModelHealthStore
from core.llm.model.model_target import ModelTarget
from core.llm.model.routing_executor import RoutingExecutor
from core.llm.observation import (
    LLM_CALL_TABLE,
    PURPOSE_GENERATE,
    SCENE_CHAT,
    LlmCallDao,
    ObservationHub,
)
from core.llm.observation.scope import llm_call_scope
from core.llm.providers.base import BaseChatClient
from core.llm.result import ChatResult, UsageStats
from core.llm.schema import ChatRequest, Message, Role
from storage.database import InMemoryDatabaseClient


# ==================== 桩件（接受 provider 契约形态，跨流式/非流式） ====================


class _SelectorStub:
    def __init__(self, targets):
        self._targets = list(targets)

    def select_chat_candidates(self, thinking, override=None, preferred_model_id=None):
        return list(self._targets)


class _Collector(BaseStreamCallback):
    """收集下游回调 + 首包事件（驱动取消时序）"""

    def __init__(self):
        self.errors = []
        self.completes = 0
        self.contents = []
        self.first_content = asyncio.Event()

    async def on_content(self, token):
        self.contents.append(token)
        if not self.first_content.is_set():
            self.first_content.set()

    async def on_complete(self):
        self.completes += 1

    async def on_error(self, error):
        self.errors.append(error)


class _ScriptClient(BaseChatClient):
    """跨流式/非流式脚本桩：chat 弹栈（Exception→抛 / ChatResult→返回）；
    stream 按脚本驱动（语义对齐 T1.4 的 _StreamScriptClient）"""

    def __init__(self, chat_script=None, stream_script=None):
        self._chat_script = list(chat_script or [])
        self._stream_script = dict(stream_script or {})

    @property
    def provider(self) -> str:
        return "p1"

    async def chat(self, request, target):
        step = self._chat_script.pop(0) if self._chat_script else ChatResult("stub-ok")
        if isinstance(step, Exception):
            raise step
        return step

    async def stream_chat(self, request, callback, target):
        for step in self._stream_script.get(target.id, [("complete",)]):
            kind = step[0]
            if kind == "content":
                await callback.on_content(step[1])
            elif kind == "usage":
                await callback.on_usage(step[1])
            elif kind == "complete":
                await callback.on_complete()
                return
            elif kind == "error":
                await callback.on_error(step[1])
                return
            elif kind == "sleep":
                await asyncio.sleep(step[1])


def _target(model_id: str, timeout_ms=None) -> ModelTarget:
    candidate = ModelCandidate(id=model_id, provider="p1", model=model_id)
    return ModelTarget(
        id=candidate.id,
        candidate=candidate,
        provider=ProviderConfig(url="https://example.com", api_key="test-key"),
        timeout_ms=timeout_ms,
    )


def _request() -> ChatRequest:
    return ChatRequest(messages=[Message(role=Role.USER, content="你好，介绍一下自己")])


def _hub_with_db():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    return db, ObservationHub(LlmCallDao(db))


def _build_service_for(client, *, targets, hub) -> RoutingLLMService:
    health_store = ModelHealthStore(failure_threshold=3, open_duration_ms=30000)
    executor = RoutingExecutor(health_store)
    return RoutingLLMService(
        _SelectorStub(targets), health_store, executor, [client],
        observation_sink=hub.emit,
    )


def _rows(db):
    return sorted(db.select_rows(LLM_CALL_TABLE), key=lambda r: r["attempt_no"])


def _assert_unpriced_invariant(row):
    """T1.8 组合不变量：任意落库行的 pricing_snapshot 必非 NULL（unpriced 占位）、cost 恒空"""
    snapshot = json.loads(row["pricing_snapshot"])
    assert snapshot == {"pricing_version": None, "status": "unpriced"}
    assert row["cost_usd"] is None


def _assert_no_unknown(rows):
    """/DonD unknown=0：集成面行不得出现 unknown 场景/目的（观测层自身健康）"""
    for row in rows:
        assert row["scene"] != "unknown" and row["purpose"] != "unknown"


# ==================== 四态 E2E ====================


def test_e2e_nonstream_success_real_usage():
    """态① 非流式成功：真实 usage 落行 + scope 全字段物化 + T1.8 固化不变量"""
    db, hub = _hub_with_db()
    client = _ScriptClient(chat_script=[ChatResult("你好，我是助手。", UsageStats(10, 5))])
    service = _build_service_for(client, targets=[_target("m1")], hub=hub)

    async def scenario():
        with llm_call_scope(scene=SCENE_CHAT, purpose=PURPOSE_GENERATE,
                            trace_id="tr-e2e-1", user_id="u-e2e"):
            result = await service.chat(_request(), tier=Tier.FAST)
        await hub.stop_and_drain()
        return result

    result = asyncio.run(scenario())
    assert str(result) == "你好，我是助手。"
    row = _rows(db)[0]
    assert row["status"] == "success" and row["is_final"] == 1
    assert row["prompt_tokens"] == 10 and row["completion_tokens"] == 5
    assert row["token_source"] == "real"
    assert row["trace_id"] == "tr-e2e-1" and row["user_id"] == "u-e2e"
    assert row["scene"] == SCENE_CHAT and row["purpose"] == PURPOSE_GENERATE
    _assert_unpriced_invariant(row)
    _assert_no_unknown([row])


def test_e2e_stream_success_with_usage_tail():
    """态② 流式成功：usage 尾包 real + TTFT/latency 遥测真实入行"""
    db, hub = _hub_with_db()
    client = _ScriptClient(stream_script={"m1": [
        ("content", "你"), ("content", "好"),
        ("usage", UsageStats(prompt_tokens=11, completion_tokens=6)),
        ("complete",),
    ]})
    service = _build_service_for(client, targets=[_target("m1")], hub=hub)
    collector = _Collector()

    async def scenario():
        with llm_call_scope(scene=SCENE_CHAT, purpose=PURPOSE_GENERATE, trace_id="tr-e2e-2"):
            await service.stream_chat(_request(), collector)
        await hub.stop_and_drain()

    asyncio.run(scenario())
    row = _rows(db)[0]
    assert row["status"] == "success" and row["is_final"] == 1
    assert row["prompt_tokens"] == 11 and row["completion_tokens"] == 6
    assert row["token_source"] == "real"
    assert row["ttft_ms"] is not None and row["ttft_ms"] >= 0
    assert row["latency_ms"] >= row["ttft_ms"]
    _assert_unpriced_invariant(row)
    _assert_no_unknown([row])


def test_e2e_stream_midstream_failure_failover_rows():
    """态③ 首包后中途失败：failed 行（部分 token 估算计成本）+ 切换后 success 终态行"""
    db, hub = _hub_with_db()
    client = _ScriptClient(stream_script={
        "m1": [("content", "你"), ("content", "好"),
               ("error", ModelClientException("broken pipe", ModelClientErrorType.NETWORK_ERROR))],
        "m2": [("content", "ok"), ("complete",)],
    })
    service = _build_service_for(client, targets=[_target("m1"), _target("m2")], hub=hub)
    collector = _Collector()

    async def scenario():
        # 镜像真实域（T1.6 调用点接线）：不包 scope 即 unknown 兜底行——漏埋点自行暴露
        with llm_call_scope(scene=SCENE_CHAT, purpose=PURPOSE_GENERATE, trace_id="tr-e2e-3"):
            await service.stream_chat(_request(), collector)
        await hub.stop_and_drain()

    asyncio.run(scenario())
    rows = _rows(db)
    failed, success = rows[0], rows[1]
    assert len(rows) == 2
    assert failed["status"] == "failed" and failed["is_final"] == 0
    assert failed["error_type"] == "network_error"
    assert failed["prompt_tokens"] > 0 and failed["completion_tokens"] > 0  # 部分输出计成本
    assert failed["token_source"] == "estimated"  # 尾包未到 → 估算
    assert success["status"] == "success" and success["is_final"] == 1
    assert failed["request_uid"] == success["request_uid"]  # 同逻辑调用归组（D4）
    assert len(collector.errors) == 1  # 中途失败照常转发下游（T1.4 已证实的真实语义）
    assert collector.contents[-1] == "ok"
    _assert_unpriced_invariant(failed)
    _assert_unpriced_invariant(success)
    _assert_no_unknown(rows)


def test_e2e_stream_cancel_canceled_row_costs_tokens():
    """态④ 上游取消：canceled 行计成本不进失败率（Q11）+ is_final 终态标"""
    db, hub = _hub_with_db()
    client = _ScriptClient(stream_script={"m1": [("content", "你"), ("sleep", 0.15), ("complete",)]})
    service = _build_service_for(client, targets=[_target("m1")], hub=hub)
    collector = _Collector()

    async def scenario():
        # 镜像真实域：scope 在任务创建点可见（contextvar 随 create_task 复制进内层 task）
        with llm_call_scope(scene=SCENE_CHAT, purpose=PURPOSE_GENERATE, trace_id="tr-e2e-4"):
            outer = asyncio.create_task(service.stream_chat(_request(), collector))
            await collector.first_content.wait()
            outer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await outer
        await hub.stop_and_drain()  # 让内层任务自然收尾，避免挂起任务告警

    asyncio.run(scenario())
    row = _rows(db)[0]
    assert row["status"] == "canceled" and row["is_final"] == 1
    assert row["completion_tokens"] > 0  # 已产出内容计入成本（canceled 照常固化）
    assert row["ttft_ms"] is not None
    _assert_unpriced_invariant(row)
    _assert_no_unknown([row])


# ==================== unknown=0 静态审计（D5 全覆盖验证） ====================

_REPO = Path(__file__).resolve().parents[2]
_CODE_DIRS = ("rag", "ingestion", "knowledge", "agent", "admin", "user", "common", "core", "app")
_SET_CALL_SCOPE_ALLOWED_FILES = {"rag/service/stream/trace_runner.py"}
_MIN_SCOPE_SITE_COUNT = 15  # T1.6 冻结清单 14 处 + scope.py docstring 示例 1 处


def _py_lines():
    for dirname in _CODE_DIRS:
        base = _REPO / dirname
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path.relative_to(_REPO).as_posix(), path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()


def test_static_audit_call_sites_declare_scene_and_purpose():
    """静态审计：调用点清单口径（G0.2 纪律）——运行时 llm_call_scope 必须显式
    scene=/purpose=；set_call_scope 仅允许 trace_runner（overlay 继承）；调用点
    数下界防增删漂移。任一运行时行出现 unknown，此测试审计口径兜底定位漏埋点。"""
    scope_sites = []
    set_scope_files = []
    for rel_path, lines in _py_lines():
        for idx, line in enumerate(lines):
            if "with llm_call_scope(" in line and "def llm_call_scope" not in line:
                window = "\n".join(lines[idx: idx + 5])  # 容许多行调用站点
                site_ok = "scene=" in window and "purpose=" in window
                scope_sites.append((rel_path, idx + 1, site_ok))
            if re.search(r"(?<![\w])set_call_scope\(", line) and "def set_call_scope" not in line:
                set_scope_files.append(rel_path)

    missing = [(rel, ln) for rel, ln, ok in scope_sites if not ok]
    assert not missing, f"调用点缺显式 scene/purpose（漏埋点→unknown 行）：{missing}"
    assert len(scope_sites) >= _MIN_SCOPE_SITE_COUNT, (
        f"调用点数跌破 T1.6 清单下界：{len(scope_sites)} < {_MIN_SCOPE_SITE_COUNT}"
    )
    assert set(set_scope_files) <= _SET_CALL_SCOPE_ALLOWED_FILES, set_scope_files
