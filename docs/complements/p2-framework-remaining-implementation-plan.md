# P2 框架尾款 Implementation Plan

> 对应 `docs/ragent-file-by-file-comparison.md` §12 P2「补框架尾款」。先计划、后实施；每步 TDD 先行，全量回归兜底。

**Goal:** 补齐对比文档登记的框架尾款缺口——消费幂等（IdempotentConsume）、RedisKeySerializer、专用配置校验器/失败分析器（RetrievalChannelConfigValidator + RetrievalConfigException）、LogSafe、LLMResponseCleaner，并完成 README / 对比文档销案。

**Architecture:** 沿既有「Java 类 → Python 模块」逐文件等价移植口径，全部为**纯逻辑/工具组件 + 单测**，无外部服务依赖：
- 消费幂等复用 `storage.cache.CacheManager`（get+set 模拟 setnx，对齐既有 D 决策「CacheManager 无原子 setnx」）；
- RedisKeySerializer 做成独立序列化器，并给 `RedisCacheManager` 增加**可选** `key_prefix`（默认空，零行为变更）；
- 检索配置校验器为纯逻辑 validator（type_reader/enabled_reader 注入），wiring 启动期仅**告警不阻断**（保持既有装配行为不变）；`RetrievalConfigException.format_failure()` 提供 Java FailureAnalyzer 同款诊断文案；
- LLMResponseCleaner / LogSafe 落 `common/util/`，集中化**仅**重构 `ingestion/util/json_response_parser.py`（语义最贴近 Java，下游 `_extract_json_body` 归一化保证最终解析等价）；其余 4 处 `_CODE_FENCE` 正则内联清理（更激进语义、有既有测试锁定）**本轮不改**，保持行为不变。

**Tech Stack:** Python 3.13 / asyncio / pytest / dataclass / re / `storage.cache.CacheManager`（redis-py 惰性）。

---

## 现状核对（2026-08-23 已核实）

| 组件 | Java 参考 | Python 现状 | 落点 |
|---|---|---|---|
| 提交幂等 | `framework/idempotent/IdempotentSubmit*` | ✅ 已实现（`common/idempotent/submit.py` + `rag/service/idempotent.py`） | 沿用其风格 |
| 消费幂等 | `framework/idempotent/IdempotentConsume{,Aspect,StatusEnum}` | ❌ 缺失 | 新建 `common/idempotent/consume.py` |
| RedisKeySerializer | `framework/cache/RedisKeySerializer.java` | ❌ 语义分散 | 新建 `storage/cache/key_serializer.py` |
| 检索配置校验器 | `rag/config/validation/{RetrievalChannelConfigValidator,RetrievalConfigFailureAnalyzer}.java` | ❌ 缺失 | 新建 `rag/retrieval/config_validation.py` |
| LLMResponseCleaner | `infra/util/LLMResponseCleaner.java` | ❌ 无集中实现（5 处内联） | 新建 `common/util/llm_response_cleaner.py` |
| LogSafe | `infra/util/LogSafe.java` | ❌ 无集中实现（1 处内联 `_preview`） | 新建 `common/util/log_safe.py` |

关键既有组件：`storage/cache/client.py`（CacheManager / MemoryCacheManager / RedisCacheManager）、`common/idempotent/submit.py`（装饰器风格范式）、`rag/retrieval/config.py`（RetrievalProperties，env 驱动）、`rag/keyword/config.py`（KeywordProperties.type none/es）、`rag/graph/config.py`（GraphProperties.type none/lightrag）、`app/wiring.py:584`（`_build_retrieval_engine` 装配点，已有 `logger`）。

---

## Task Breakdown

### Task 1: LogSafe（日志安全截断工具）

**Files:**
- Create: `common/util/log_safe.py`
- Modify: `common/util/__init__.py`（导出）
- Test: `tests/test_log_safe_unit.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_log_safe_unit.py
# -*- coding: utf-8 -*-
"""P2 LogSafe 单测：common/util/log_safe.py（对应 Java infra/util/LogSafe）"""
from common.util.log_safe import preview


class TestPreview:
    def test_none_returns_none(self):
        assert preview(None) is None

    def test_short_text_unchanged(self):
        assert preview("hello") == "hello"

    def test_long_text_truncated_with_suffix(self):
        raw = "x" * 600
        out = preview(raw)
        assert out.startswith("x" * 500)
        assert out == "x" * 500 + "...(truncated, total 600 chars)"

    def test_exactly_max_unchanged(self):
        assert preview("y" * 500) == "y" * 500

    def test_custom_max(self):
        raw = "abcdef"
        assert preview(raw, 3) == "abc...(truncated, total 6 chars)"

    def test_custom_max_large(self):
        assert preview("abc", 10) == "abc"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_log_safe_unit.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'common.util.log_safe'`）

- [ ] **Step 3: 最小实现**

```python
# common/util/log_safe.py
# -*- coding: utf-8 -*-
"""
common.util.log_safe - 日志安全工具（对应 Java infra/util/LogSafe）
把可能较长、可能含用户/工具参数的原始响应截断后再落日志，避免日志膨胀与敏感信息完整外泄。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.infra.util.LogSafe
"""
from __future__ import annotations

from typing import Optional

# 默认预览长度（对齐 Java LogSafe.DEFAULT_MAX）
DEFAULT_MAX = 500


def preview(raw: Optional[str], max: Optional[int] = None) -> Optional[str]:
    """按 max 截断原始文本；超出部分以省略号 + 总长度提示替代（对齐 Java preview）

    Args:
        raw: 原始文本；None 原样返回
        max: 截断上限，缺省 DEFAULT_MAX（500）
    """
    if raw is None:
        return None
    limit = DEFAULT_MAX if max is None else max
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"...(truncated, total {len(raw)} chars)"
```

- [ ] **Step 4: 导出到包（仅导出本任务符号；`strip_markdown_code_fence` 在 Task 2 Step 4 追加）**

```python
# common/util/__init__.py（在现有内容后追加）
from common.util.log_safe import preview

__all__ = [
    "SnowflakeIdGenerator",
    "default_generator",
    "preview",
]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_log_safe_unit.py -q`
Expected: PASS（6 passed）

- [ ] **Step 6: Commit**

```bash
git add common/util/log_safe.py common/util/__init__.py tests/test_log_safe_unit.py
git commit -m "feat: add LogSafe preview truncation util (P2)"
```

### Task 2: LLMResponseCleaner（剥离 Markdown 围栏）+ json_response_parser 委托

**Files:**
- Create: `common/util/llm_response_cleaner.py`
- Modify: `ingestion/util/json_response_parser.py`（`_strip_markdown_code_fence` 委托）
- Test: `tests/test_llm_response_cleaner_unit.py`、`tests/test_json_response_parser_unit.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_response_cleaner_unit.py
# -*- coding: utf-8 -*-
"""P2 LLMResponseCleaner 单测：common/util/llm_response_cleaner.py（对应 Java infra/util/LLMResponseCleaner）"""
from common.util.llm_response_cleaner import strip_markdown_code_fence


class TestStripMarkdownCodeFence:
    def test_none_returns_none(self):
        assert strip_markdown_code_fence(None) is None

    def test_no_fence_unchanged(self):
        assert strip_markdown_code_fence('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert strip_markdown_code_fence(raw) == '{"a": 1}'

    def test_bare_fence(self):
        assert strip_markdown_code_fence("```\nhello\n```") == "hello"

    def test_language_tag(self):
        assert strip_markdown_code_fence("```python\nx=1\n```") == "x=1"

    def test_leading_trailing_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        assert strip_markdown_code_fence(raw) == '{"a": 1}'

    def test_fence_without_newline(self):
        assert strip_markdown_code_fence("```json{\"a\": 1}```") == '{"a": 1}'

    def test_middle_fence_not_stripped(self):
        raw = "pre ``` nope"
        assert strip_markdown_code_fence(raw) == "pre ``` nope"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_llm_response_cleaner_unit.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小实现**

```python
# common/util/llm_response_cleaner.py
# -*- coding: utf-8 -*-
"""
common.util.llm_response_cleaner - LLM 输出清理（对应 Java infra/util/LLMResponseCleaner）
模型偶发包裹 Markdown 代码围栏（```json ... ```）时剥离，仅处理前导/尾随围栏。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.infra.util.LLMResponseCleaner
"""
from __future__ import annotations

import re
from typing import Optional

# 前导围栏：``` 后接可选语言标记（[\w-]*）与可选换行（对齐 Java ^```[\w-]*\s*\n?）
_LEADING_CODE_FENCE = re.compile(r"^```[\w-]*\s*\n?")
# 尾随围栏：可选换行 + ``` + 行尾空白（对齐 Java \n?```\s*$）
_TRAILING_CODE_FENCE = re.compile(r"\n?```\s*$")


def strip_markdown_code_fence(raw: Optional[str]) -> Optional[str]:
    """剥离 Markdown 代码块围栏（对齐 Java stripMarkdownCodeFence）

    None → None；先 trim，剥前导 ```[lang] 与尾随 ```，最后再 trim。
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    cleaned = _LEADING_CODE_FENCE.sub("", cleaned)
    cleaned = _TRAILING_CODE_FENCE.sub("", cleaned)
    return cleaned.strip()
```

- [ ] **Step 4: json_response_parser 委托共享实现（保留函数签名，行为等价）**

`ingestion/util/json_response_parser.py`：在 `_parse_json_element` 前的 `_strip_markdown_code_fence` 改为委托：

```python
def _strip_markdown_code_fence(raw: str) -> str:
    """剥离 markdown 代码围栏（委托 common.util.llm_response_cleaner，对齐 Java stripMarkdownCodeFence）"""
    return strip_markdown_code_fence(raw) or ""
```

文件顶部追加导入（原 `_strip_markdown_code_fence` 函数体删除）：

```python
from common.util.llm_response_cleaner import strip_markdown_code_fence
```

并在 `common/util/__init__.py` 追加导出：

```python
from common.util.llm_response_cleaner import strip_markdown_code_fence

__all__ = [
    "SnowflakeIdGenerator",
    "default_generator",
    "preview",
    "strip_markdown_code_fence",
]
```

> 说明：原内联实现只剥「首行围栏 + 尾围栏」，与 Java 语义一致；委托后由 `_extract_json_body`（截取首个 `{`/`[` 至末个 `}`/`]`）继续归一化，最终解析结果等价（如无换行的 ` ```{...}``` ` 亦被提取归一）。

- [ ] **Step 5: 补 json_response_parser 委托回归测试**

```python
# tests/test_json_response_parser_unit.py
# -*- coding: utf-8 -*-
"""P2 json_response_parser 委托回归：ingestion/util/json_response_parser.py"""
from ingestion.util.json_response_parser import parse_object, parse_string_list


class TestParseWithFence:
    def test_parse_object_with_json_fence(self):
        assert parse_object("```json\n{\"a\": 1}\n```") == {"a": 1}

    def test_parse_string_list_with_bare_fence(self):
        assert parse_string_list("```\n[\"x\", \"y\"]\n```") == ["x", "y"]

    def test_parse_object_empty(self):
        assert parse_object(None) == {}

    def test_parse_string_list_bad(self):
        assert parse_string_list("not json") == []
```

- [ ] **Step 6: 运行测试确认通过 + 全量回归兜底**

Run: `python -m pytest tests/test_llm_response_cleaner_unit.py tests/test_json_response_parser_unit.py -q`
Expected: PASS（8 + 4 = 12 passed）

Run: `python -m pytest tests -q`
Expected: 全量回归通过（基线 620 + 新增 12 + Task1 6 = 638 附近，无失败）

- [ ] **Step 7: Commit**

```bash
git add common/util/llm_response_cleaner.py ingestion/util/json_response_parser.py \
        tests/test_llm_response_cleaner_unit.py tests/test_json_response_parser_unit.py
git commit -m "feat: add LLMResponseCleaner util, delegate json_response_parser (P2)"
```

### Task 3: RedisKeySerializer + RedisCacheManager 可选前缀

**Files:**
- Create: `storage/cache/key_serializer.py`
- Modify: `storage/cache/client.py`（`RedisCacheManager` 加可选 `key_prefix`）
- Test: `tests/test_redis_key_serializer_unit.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_redis_key_serializer_unit.py
# -*- coding: utf-8 -*-
"""P2 RedisKeySerializer 单测：storage/cache/key_serializer.py + RedisCacheManager key_prefix"""
import pytest

from storage.cache import RedisCacheManager
from storage.cache.key_serializer import RedisKeySerializer


class TestRedisKeySerializer:
    def test_serialize_with_prefix(self):
        ser = RedisKeySerializer("rag:")
        assert ser.serialize("kb:1") == b"rag:kb:1"

    def test_serialize_no_prefix(self):
        ser = RedisKeySerializer()
        assert ser.serialize("kb:1") == b"kb:1"

    def test_deserialize_utf8(self):
        ser = RedisKeySerializer("rag:")
        assert ser.deserialize(b"rag:kb:1") == "rag:kb:1"

    def test_key_prefix_property(self):
        assert RedisKeySerializer("x:").key_prefix == "x:"
        assert RedisKeySerializer().key_prefix == ""


class _FakeRedis:
    """记录写入键/值的最简 Redis 桩（仅覆盖 get/set/delete/ex）"""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def delete(self, key):
        return self.store.pop(key, None) is not None


class TestRedisCacheManagerPrefix:
    @pytest.mark.asyncio
    async def test_prefix_applied_on_all_ops(self):
        redis = _FakeRedis()
        mgr = RedisCacheManager(redis=redis, key_prefix="app:")
        await mgr.set("k", {"a": 1}, ttl=60)
        assert redis.store == {"app:k": '{"a": 1}'}  # 物理键带前缀
        assert await mgr.get("k") == {"a": 1}
        assert await mgr.delete("k") is True
        assert redis.store == {}

    @pytest.mark.asyncio
    async def test_no_prefix_behavior_unchanged(self):
        redis = _FakeRedis()
        mgr = RedisCacheManager(redis=redis)
        await mgr.set("k", 1)
        assert redis.store == {"k": "1"}
        assert await mgr.get("k") == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_redis_key_serializer_unit.py -q`
Expected: FAIL（`ModuleNotFoundError` / `TypeError: unexpected keyword argument 'key_prefix'`）

- [ ] **Step 3: 最小实现（key_serializer.py）**

```python
# storage/cache/key_serializer.py
# -*- coding: utf-8 -*-
"""
storage.cache.key_serializer - Redis Key 序列化器（对应 Java framework/cache/RedisKeySerializer）

序列化 = keyPrefix + key 的 UTF-8 字节；反序列化 = UTF-8 字符串。
RedisCacheManager 注入 key_prefix 时用本序列化器统一加前缀（默认空前缀 = 行为不变）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.cache.RedisKeySerializer
"""
from __future__ import annotations


class RedisKeySerializer:
    """Redis Key 序列化：serialize = keyPrefix + key 的 UTF-8 字节；deserialize = UTF-8 字符串"""

    def __init__(self, key_prefix: str = ""):
        self._key_prefix = key_prefix or ""

    @property
    def key_prefix(self) -> str:
        return self._key_prefix

    def serialize(self, key: str) -> bytes:
        """序列化（对齐 Java serialize：keyPrefix + key 转 UTF-8 字节）"""
        return (self._key_prefix + key).encode("utf-8")

    def deserialize(self, data: bytes) -> str:
        """反序列化（对齐 Java deserialize：UTF-8 解码为字符串）"""
        return data.decode("utf-8")
```

- [ ] **Step 4: RedisCacheManager 支持可选 key_prefix**

`storage/cache/client.py` 的 `RedisCacheManager.__init__` 增加参数并用序列化器加前缀：

```python
def __init__(self, redis: Any = None, codec: Optional[CacheCodec] = None, key_prefix: str = ""):
    if redis is None:
        raise ValueError("RedisCacheManager 需要注入 redis.asyncio.Redis 客户端")
    try:
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import RedisError
    except ImportError as exc:  # 惰性加载：未安装 redis-py 时给出明确指引
        raise ImportError(
            "RedisCacheManager 依赖 redis-py（redis>=5.0,<6.0），请先安装"
        ) from exc
    self._redis = redis
    self._codec = codec or CacheCodec()
    # 注意：redis.exceptions.ConnectionError 是 RedisError 的子类，两者同捕对齐计划约束
    self._redis_error = RedisError
    self._connection_error = RedisConnectionError
    from storage.cache.key_serializer import RedisKeySerializer

    self._key_serializer = RedisKeySerializer(key_prefix)

def _real_key(self, key: str) -> str:
    """物理 Redis 键：经 RedisKeySerializer 加前缀（空前缀 = 原键，行为不变）"""
    return self._key_serializer.serialize(key).decode("utf-8")
```

再把 `get`/`set`/`delete` 三处的 `self._redis.get(key)` → `self._redis.get(self._real_key(key))`、`self._redis.set(key, ...)` → `self._redis.set(self._real_key(key), ...)`、`self._redis.delete(key)` → `self._redis.delete(self._real_key(key))`。

- [ ] **Step 5: 运行测试确认通过 + 全量回归兜底**

Run: `python -m pytest tests/test_redis_key_serializer_unit.py -q`
Expected: PASS（4 + 2 = 6 passed，含 `pytest.mark.asyncio`）

Run: `python -m pytest tests -q`
Expected: 全量回归通过（无失败；RedisCacheManager 默认空前缀行为不变）

> 若 `pytest-asyncio` 未启用，将两个 asyncio 用例改为 `asyncio.run(...)` 包裹（与 `test_idempotent_submit_unit.py` 的 `_run` 助手一致）。

- [ ] **Step 6: Commit**

```bash
git add storage/cache/key_serializer.py storage/cache/client.py tests/test_redis_key_serializer_unit.py
git commit -m "feat: add RedisKeySerializer with optional prefix on RedisCacheManager (P2)"
```

### Task 4: IdempotentConsume（消费幂等）

**Files:**
- Create: `common/idempotent/consume.py`（Status 枚举 + Guard + 装饰器）
- Modify: `common/idempotent/__init__.py`（导出）
- Test: `tests/test_idempotent_consume_unit.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_idempotent_consume_unit.py
# -*- coding: utf-8 -*-
"""P2 消费幂等单测：common/idempotent/consume.py（对应 Java @IdempotentConsume + IdempotentConsumeAspect）

对齐 Java Lua SET NX GET PX 语义（CacheManager get+set 模拟，对齐既有 D 决策）：
    - 无状态（None）→ 置 CONSUMING 后执行 → 置 CONSUMED；
    - 已有 CONSUMING("0") → 消费中重复 → ClientException（延迟重试）；
    - 已有 CONSUMED("1") → 已完成 → 跳过（fn 不执行，返回 None）；
    - 执行异常 → 删除 key（可重试）。
"""
import asyncio

import pytest

from common.exception.business import ClientException
from common.idempotent.consume import (
    IdempotentConsumeGuard,
    IdempotentConsumeStatus,
    get_guard,
    idempotent_consume,
    set_guard,
)
from storage.cache import MemoryCacheManager


@pytest.fixture(autouse=True)
def _reset_guard():
    set_guard(None)
    yield
    set_guard(None)


def _injected_guard(key_timeout: float = 60) -> IdempotentConsumeGuard:
    guard = IdempotentConsumeGuard(cache=MemoryCacheManager(), key_timeout=key_timeout)
    set_guard(guard)
    return guard


def _run(coro):
    return asyncio.run(coro)


class TestIdempotentConsumeStatus:
    def test_consuming_is_error(self):
        assert IdempotentConsumeStatus.is_error("0") is True
        assert IdempotentConsumeStatus.is_error("1") is False
        assert IdempotentConsumeStatus.is_error(None) is False


class TestConsumeAsync:
    def test_first_consume_executes_and_marks_consumed(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:1")
        async def handle():
            calls.append(1)
            return "ok"

        assert _run(handle()) == "ok"
        assert len(calls) == 1
        assert _run(guard._cache.get("msg:1")) == "1"  # CONSUMED

    def test_consuming_duplicate_raises(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:2")
        async def handle():
            calls.append(1)
            return "ok"

        _run(guard._cache.set("msg:2", IdempotentConsumeStatus.CONSUMING.value, ttl=60))
        with pytest.raises(ClientException) as exc:
            _run(handle())
        assert "幂等标识：msg:2" in str(exc.value)
        assert len(calls) == 0

    def test_consumed_skips(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:3")
        async def handle():
            calls.append(1)
            return "ok"

        _run(guard._cache.set("msg:3", IdempotentConsumeStatus.CONSUMED.value, ttl=60))
        assert _run(handle()) is None
        assert len(calls) == 0

    def test_exception_deletes_key_for_retry(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:4")
        async def handle():
            calls.append(1)
            raise ValueError("boom")

        with pytest.raises(ValueError):
            _run(handle())
        assert _run(guard._cache.get("msg:4")) is None  # 已删除可重试
        assert len(calls) == 1

    def test_key_prefix_composition(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key_prefix="order:", key="msg:5")
        async def handle():
            calls.append(1)
            return "ok"

        _run(handle())
        assert _run(guard._cache.get("order:msg:5")) == "1"
        assert _run(guard._cache.get("msg:5")) is None

    def test_key_fn_resolution(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key_fn=lambda args, kwargs: f"msg:{args[0]}")
        async def handle(msg_id):
            calls.append(1)
            return "ok"

        _run(handle("a"))
        assert _run(guard._cache.get("msg:a")) == "1"
        assert len(calls) == 1


class TestConsumeSync:
    def test_first_consume_executes(self):
        _injected_guard()
        calls = []

        @idempotent_consume(key="msg:s1")
        def handle():
            calls.append(1)
            return "ok"

        assert handle() == "ok"
        assert len(calls) == 1

    def test_consuming_duplicate_raises(self):
        guard = _injected_guard()
        calls = []

        @idempotent_consume(key="msg:s2")
        def handle():
            calls.append(1)
            return "ok"

        _run(guard._cache.set("msg:s2", IdempotentConsumeStatus.CONSUMING.value, ttl=60))
        with pytest.raises(ClientException):
            handle()
        assert len(calls) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_idempotent_consume_unit.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小实现**

```python
# common/idempotent/consume.py
# -*- coding: utf-8 -*-
"""
common.idempotent.consume - 消费幂等装饰器（对应 Java @IdempotentConsume + IdempotentConsumeAspect + IdempotentConsumeStatusEnum）

防止消息消费者重复消费：以「状态令牌」判定
    - CONSUMING("0")：消费中 → 重复消费，raise ClientException（等待延迟重试）；
    - CONSUMED("1")：已完成 → 直接跳过（返回 None）；
    - 无状态：置 CONSUMING → 执行 body → 置 CONSUMED；执行异常 → 删除令牌（可重试）。

对齐 Java Lua `SET key value NX GET PX expire_ms` 语义；CacheManager 无原子 setnx，
以 get+set 模拟（对齐既有 D 决策），单实例/进程内有效，跨实例原子性由 P6 real 栈 Redis 强语义兜底。

key 解析（对齐 Java keyPrefix + SpEL key，Python 用 key_fn 等价）：
    - key：显式防重令牌键（与 key_prefix 拼接）；
    - key_fn：(args, kwargs) → 稳定业务键（替代 SpEL 表达式）；
    - 均未提供：回落 func 签名 + 参数 md5 的稳定键。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentConsume
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentConsumeAspect
    - com.nageoffer.ai.ragent.framework.idempotent.IdempotentConsumeStatusEnum
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional

from common.exception.business import ClientException
from storage.cache import CacheManager, MemoryCacheManager

# 缺省防重令牌 TTL（秒，对齐 Java keyTimeout 默认 3600）
DEFAULT_KEY_TIMEOUT = 3600

# 全局注入槽（对齐 audit/support/decorator 注册模式）
_guard: Optional["IdempotentConsumeGuard"] = None

# sync 路径的进程内状态表（仅单进程近似；跨进程见文档限制）
_SYNC_STATES: Dict[str, str] = {}
_SYNC_LOCK = threading.Lock()


class IdempotentConsumeStatus(Enum):
    """幂等 MQ 消费状态（对齐 Java IdempotentConsumeStatusEnum）"""

    CONSUMING = "0"  # 消费中
    CONSUMED = "1"  # 已消费

    @classmethod
    def is_error(cls, code: Optional[str]) -> bool:
        """消费中视为失败（对齐 Java isError）"""
        return code == cls.CONSUMING.value


class IdempotentConsumeGuard:
    """消费幂等守卫（对应 Java IdempotentConsumeAspect 核心逻辑）"""

    def __init__(
        self,
        cache: Optional[CacheManager] = None,
        key_timeout: float = DEFAULT_KEY_TIMEOUT,
    ):
        self._cache: CacheManager = cache or MemoryCacheManager()
        self._key_timeout = key_timeout

    async def consume(
        self,
        key: str,
        fn: Callable[[], Any],
        async_fn: bool = False,
    ) -> Any:
        """幂等消费：CONSUMING → 抛错；CONSUMED → 跳过返回 None；否则执行并置 CONSUMED

        async_fn=True 时 fn 为协程函数（await 执行）；否则 fn 为普通可调用。
        """
        current = await self._cache.get(key)
        if current == IdempotentConsumeStatus.CONSUMING.value:
            raise ClientException(f"消息消费者幂等异常，幂等标识：{key}")
        if current == IdempotentConsumeStatus.CONSUMED.value:
            return None
        await self._cache.set(key, IdempotentConsumeStatus.CONSUMING.value, ttl=self._key_timeout)
        try:
            result = await fn() if async_fn else fn()
        except Exception:
            await self._cache.delete(key)
            raise
        await self._cache.set(key, IdempotentConsumeStatus.CONSUMED.value, ttl=self._key_timeout)
        return result


def set_guard(guard: Optional[IdempotentConsumeGuard]) -> None:
    """注册全局消费幂等守卫（wiring 注入；None 解除用于测试隔离）"""
    global _guard
    _guard = guard


def get_guard(key_timeout: float = DEFAULT_KEY_TIMEOUT) -> IdempotentConsumeGuard:
    """取全局守卫；未注册 → 懒建内存兜底（保证装饰器立即可用）"""
    global _guard
    if _guard is None:
        _guard = IdempotentConsumeGuard(
            cache=MemoryCacheManager(), key_timeout=key_timeout
        )
    return _guard


def _args_md5(*args) -> str:
    """参数稳定序列化 md5（对齐 submit 的 _args_md5）"""
    payload = json.dumps(list(args), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def _resolve_consume_key(
    func: Callable[..., Any],
    key: Optional[str],
    key_fn: Optional[Callable[[tuple, dict], Any]],
    args: tuple,
    kwargs: dict,
) -> str:
    if key:
        return key
    if key_fn is not None:
        value = key_fn(args, kwargs)
        return str(value)
    path = f"{func.__module__}.{func.__qualname__}"
    return f"{path}:md5:{_args_md5(*args)}"


def idempotent_consume(
    key_prefix: str = "",
    key: Optional[str] = None,
    key_fn: Optional[Callable[[tuple, dict], Any]] = None,
    key_timeout: float = DEFAULT_KEY_TIMEOUT,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """消费幂等装饰器（async / sync 双兼容）

    Args:
        key_prefix:  防重令牌 key 前缀（对齐 Java keyPrefix，默认空）
        key:         显式防重令牌键（对齐 Java key，默认空走 key_fn/签名兜底）
        key_fn:      (args, kwargs) → 稳定业务键（替代 Java SpEL 表达式）
        key_timeout: 令牌过期秒数（对齐 Java keyTimeout，默认 3600）
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            guard = get_guard(key_timeout)
            full_key = key_prefix + _resolve_consume_key(func, key, key_fn, args, kwargs)
            return await guard.consume(full_key, lambda: func(*args, **kwargs), async_fn=True)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            full_key = key_prefix + _resolve_consume_key(func, key, key_fn, args, kwargs)
            with _SYNC_LOCK:
                state = _SYNC_STATES.get(full_key)
                if state == IdempotentConsumeStatus.CONSUMING.value:
                    raise ClientException(f"消息消费者幂等异常，幂等标识：{full_key}")
                if state == IdempotentConsumeStatus.CONSUMED.value:
                    return None
                _SYNC_STATES[full_key] = IdempotentConsumeStatus.CONSUMING.value
            try:
                result = func(*args, **kwargs)
            except Exception:
                with _SYNC_LOCK:
                    _SYNC_STATES.pop(full_key, None)
                raise
            with _SYNC_LOCK:
                _SYNC_STATES[full_key] = IdempotentConsumeStatus.CONSUMED.value
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator
```

- [ ] **Step 4: 导出到包**

```python
# common/idempotent/__init__.py（替换为完整内容；原文件仅有 docstring）
# -*- coding: utf-8 -*-
"""
common.idempotent - framework 幂等设施（对应 Java framework/idempotent）
"""
from common.idempotent.consume import (
    IdempotentConsumeGuard,
    IdempotentConsumeStatus,
    idempotent_consume,
)
from common.idempotent.submit import idempotent_submit, set_guard as set_submit_guard

__all__ = [
    "IdempotentConsumeGuard",
    "IdempotentConsumeStatus",
    "idempotent_consume",
    "idempotent_submit",
    "set_submit_guard",
]
```

- [ ] **Step 5: 运行测试确认通过 + 全量回归兜底**

Run: `python -m pytest tests/test_idempotent_consume_unit.py -q`
Expected: PASS（3 + 6 + 2 = 11 passed）

Run: `python -m pytest tests -q`
Expected: 全量回归通过（无失败）

- [ ] **Step 6: Commit**

```bash
git add common/idempotent/consume.py common/idempotent/__init__.py tests/test_idempotent_consume_unit.py
git commit -m "feat: add IdempotentConsume consumer idempotency decorator (P2)"
```

### Task 5: 检索通道配置校验器（RetrievalChannelConfigValidator + RetrievalConfigException）

**Files:**
- Create: `rag/retrieval/config_validation.py`
- Modify: `app/wiring.py`（`_build_retrieval_engine` 启动期告警校验）
- Test: `tests/test_retrieval_config_validator_unit.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_retrieval_config_validator_unit.py
# -*- coding: utf-8 -*-
"""P2 检索通道配置校验器单测：rag/retrieval/config_validation.py（对应 Java RetrievalChannelConfigValidator + FailureAnalyzer）"""
import pytest

from rag.retrieval.config_validation import (
    RetrievalConfigException,
    Violation,
    validate,
    validate_env,
)


def _readers(type_map, enabled_map):
    return (
        lambda type_key: type_map.get(type_key),
        lambda enabled_key: enabled_map.get(enabled_key, False),
    )


class TestValidate:
    def test_keyword_enabled_type_none_violation(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "none"}, {"RAGENT_RETRIEVAL_KEYWORD": True}
        )
        violations = validate(type_reader, enabled_reader)
        assert len(violations) == 1
        assert violations[0].channel_label == "关键词检索"
        assert violations[0].required_type == "es"

    def test_graph_enabled_type_none_violation(self):
        type_reader, enabled_reader = _readers(
            {"graph.type": None}, {"RAGENT_RETRIEVAL_GRAPH": True}
        )
        violations = validate(type_reader, enabled_reader)
        assert len(violations) == 1
        assert violations[0].channel_label == "图谱检索"
        assert violations[0].actual_type == ""

    def test_keyword_type_es_no_violation(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "es", "graph.type": "none"}, {"RAGENT_RETRIEVAL_KEYWORD": True}
        )
        assert validate(type_reader, enabled_reader) == []

    def test_type_case_insensitive(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "ES"}, {"RAGENT_RETRIEVAL_KEYWORD": True}
        )
        assert validate(type_reader, enabled_reader) == []

    def test_all_disabled_no_violation(self):
        type_reader, enabled_reader = _readers({}, {})
        assert validate(type_reader, enabled_reader) == []

    def test_both_violations_collected_together(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "none", "graph.type": ""},
            {"RAGENT_RETRIEVAL_KEYWORD": True, "RAGENT_RETRIEVAL_GRAPH": True},
        )
        assert len(validate(type_reader, enabled_reader)) == 2

    def test_enabled_false_backend_off_no_violation(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "none"}, {"RAGENT_RETRIEVAL_KEYWORD": False}
        )
        assert validate(type_reader, enabled_reader) == []


class TestValidateEnv:
    def test_env_driven_violation(self, monkeypatch):
        monkeypatch.delenv("RAGENT_KEYWORD_TYPE", raising=False)
        monkeypatch.delenv("RAGENT_GRAPH_TYPE", raising=False)
        monkeypatch.setenv("RAGENT_RETRIEVAL_KEYWORD", "1")
        monkeypatch.setenv("RAGENT_RETRIEVAL_GRAPH", "1")
        violations = validate_env()
        labels = {v.channel_label for v in violations}
        assert labels == {"关键词检索", "图谱检索"}

    def test_env_type_set_no_violation(self, monkeypatch):
        monkeypatch.setenv("RAGENT_KEYWORD_TYPE", "es")
        monkeypatch.setenv("RAGENT_GRAPH_TYPE", "lightrag")
        monkeypatch.setenv("RAGENT_RETRIEVAL_KEYWORD", "1")
        monkeypatch.setenv("RAGENT_RETRIEVAL_GRAPH", "1")
        assert validate_env() == []


class TestRetrievalConfigException:
    def test_format_failure_contains_actions(self):
        violations = [Violation(
            channel_label="关键词检索", type_key="keyword.type", actual_type="none",
            required_type="es", enabled_key="RAGENT_RETRIEVAL_KEYWORD",
            enable_hint="并配置 rag.keyword.es.*",
        )]
        exc = RetrievalConfigException(violations)
        assert "检索通道配置存在矛盾（1 项）" in exc.format_failure()
        assert "设 keyword.type=es" in exc.format_failure()
        assert "设 RAGENT_RETRIEVAL_KEYWORD=false" in exc.format_failure()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_retrieval_config_validator_unit.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 最小实现**

```python
# rag/retrieval/config_validation.py
# -*- coding: utf-8 -*-
"""
rag.retrieval.config_validation - 检索通道「后端装配 vs 通道启用」一致性校验
（对应 Java rag/config/validation/{RetrievalChannelConfigValidator,RetrievalConfigException,RetrievalConfigFailureAnalyzer}）

两层完全正交（对齐 Java 注释）：
    - 后端装配：keyword.type / graph.type 决定后端实现是否注册（none 或非法值 → 通道类根本不进容器）；
    - 通道启用：RAGENT_RETRIEVAL_KEYWORD / RAGENT_RETRIEVAL_GRAPH（RetrievalProperties）在检索期被读取。
有效参与 = 后端已装配 AND 通道已启用。故「type=none 但 enabled=true」是哑标志：用户以为开了该路检索，
实际通道类都没注册——本校验器专抓这种单向矛盾（反过来的 type=es 但 enabled=false 合法，不报）。

纯逻辑、不依赖 wiring（type_reader / enabled_reader 注入），便于单测；wiring 启动期调用 validate_env()
仅告警不阻断（保持既有装配行为不变）；严格校验可 raise RetrievalConfigException（format_failure 对齐
FailureAnalyzer 的 Description / Action 渲染）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from rag.retrieval.config import RetrievalProperties


@dataclass(frozen=True)
class Violation:
    """一条「后端未装配却开了检索通道」的矛盾（对齐 Java Violation record）"""

    channel_label: str
    type_key: str
    actual_type: str
    required_type: str
    enabled_key: str
    enable_hint: str


@dataclass(frozen=True)
class ChannelSpec:
    """待校验的通道规格：新增一路检索只需在 _SPECS 加一条"""

    label: str
    type_key: str
    required_type: str
    enabled_key: str
    enable_hint: str


_SPECS: List[ChannelSpec] = [
    ChannelSpec("关键词检索", "keyword.type", "es", "RAGENT_RETRIEVAL_KEYWORD", "并配置 rag.keyword.es.*"),
    ChannelSpec("图谱检索", "graph.type", "lightrag", "RAGENT_RETRIEVAL_GRAPH", "并确保 LightRAG 服务可达（rag.graph.lightrag.base-url）"),
]

# type 配置键 → 环境变量（Python 无 Spring 配置，type 走 env，默认 none）
_TYPE_ENV = {
    "keyword.type": "RAGENT_KEYWORD_TYPE",
    "graph.type": "RAGENT_GRAPH_TYPE",
}


def _read_type_env(type_key: str) -> str:
    return os.environ.get(_TYPE_ENV[type_key], "none") or "none"


def validate(
    type_reader: Callable[[str], Optional[str]],
    enabled_reader: Callable[[str], bool],
) -> List[Violation]:
    """校验所有通道，一次性收集全部违规（不撞到第一条就停，便于一次改完）

    Args:
        type_reader:    读取后端类型键的实际值（不存在返回 None）
        enabled_reader: 读取通道启用开关（不存在按 False）
    """
    violations: List[Violation] = []
    for spec in _SPECS:
        actual_type = type_reader(spec.type_key)
        # 后端未装配：type 缺省 / 空白 / 非所需值（大小写不敏感，对齐 @ConditionalOnProperty 判定）
        backend_off = (
            actual_type is None
            or not actual_type.strip()
            or actual_type.strip().lower() != spec.required_type
        )
        if backend_off and enabled_reader(spec.enabled_key):
            violations.append(
                Violation(
                    spec.label,
                    spec.type_key,
                    actual_type if actual_type else "",
                    spec.required_type,
                    spec.enabled_key,
                    spec.enable_hint,
                )
            )
    return violations


def validate_env() -> List[Violation]:
    """从环境变量 + RetrievalProperties 校验（wiring 启动期调用）"""
    props = RetrievalProperties.from_env()
    enabled_map = {
        "RAGENT_RETRIEVAL_KEYWORD": props.keyword_enabled,
        "RAGENT_RETRIEVAL_GRAPH": props.graph_enabled,
    }
    return validate(_read_type_env, lambda key: enabled_map.get(key, False))


class RetrievalConfigException(RuntimeError):
    """检索通道配置矛盾异常（对应 Java RetrievalConfigException + FailureAnalyzer 渲染）"""

    def __init__(self, violations: List[Violation]):
        self.violations = violations
        super().__init__(self.format_failure())

    def format_failure(self) -> str:
        """渲染诊断文案（对齐 Java RetrievalConfigFailureAnalyzer 的 Description / Action）"""
        description = f"检索通道配置存在矛盾（{len(self.violations)} 项）："
        action = "按需二选一修正："
        for index, v in enumerate(self.violations, start=1):
            actual = v.actual_type if v.actual_type else "<未设置>"
            description += (
                f"\n  {index}. {v.enabled_key}=true，但{v.channel_label}后端未启用"
                f"（{v.type_key}={actual}，需为 {v.required_type}）"
                f"\n     → 该通道不会被注册，启用标志形同虚设"
            )
            action += (
                f"\n  {v.channel_label}："
                f"\n    • 启用该检索：设 {v.type_key}={v.required_type} {v.enable_hint}"
                f"\n    • 关闭该通道：设 {v.enabled_key}=false"
            )
        return f"{description}\n{action}"
```

- [ ] **Step 4: wiring 启动期告警校验（不阻断）**

`app/wiring.py` 的 `_build_retrieval_engine` 内、`props = self.retrieval_properties or RetrievalProperties.from_env()` 之后插入：

```python
        # 检索通道配置一致性校验（对齐 Java RetrievalConfigFailureAnalyzer；告警不阻断，保持既有装配行为）
        from rag.retrieval.config_validation import RetrievalConfigException, validate_env

        _violations = validate_env()
        if _violations:
            logger.warning("检索通道配置矛盾（启动不阻断）: %s", RetrievalConfigException(_violations).format_failure())
```

- [ ] **Step 5: 运行测试确认通过 + 全量回归兜底**

Run: `python -m pytest tests/test_retrieval_config_validator_unit.py -q`
Expected: PASS（7 + 2 + 1 = 10 passed）

Run: `python -m pytest tests -q`
Expected: 全量回归通过（wiring 告警不阻断，既有装配测试不受影响）

- [ ] **Step 6: Commit**

```bash
git add rag/retrieval/config_validation.py app/wiring.py tests/test_retrieval_config_validator_unit.py
git commit -m "feat: add retrieval channel config validator + startup diagnostics (P2)"
```

### Task 6: 文档收官 + 全量回归

**Files:**
- Modify: `README.md`（工具/清理行）
- Modify: `docs/ragent-file-by-file-comparison.md`（framework / infra util / config validation 行 + §5 缺口 + §12 P2 销案）
- Modify: `docs/complements/p2-framework-remaining-implementation-plan.md`（§7 收官记录）

- [ ] **Step 1: README 销案**

`README.md` 第 34 行：

```markdown
| 工具/清理 | `LLMResponseCleaner` 输出清洗 + `LogSafe` 日志脱敏 | ✅ 已实现（P2） |
```

- [ ] **Step 2: 对比文档销案**

- 总体结论「五大缺口」第 5 条（原含「消费幂等、专用配置校验器、日志脱敏仍未完成」）改为仅剩部署/样例类缺口，并注明 P2 已收官；
- §3 `framework/` 行：`消费幂等和 RedisKeySerializer 缺失` → `消费幂等 + RedisKeySerializer 已补齐（P2）`；
- framework 表：
  - `RedisKeySerializer.java` 行：🟡 → ✅（`storage/cache/key_serializer.py` + RedisCacheManager 可选前缀）；
  - `IdempotentConsume{.java,Aspect,StatusEnum}` 三行：❌ → ✅（`common/idempotent/consume.py`）；
- infra util 表：`LLMResponseCleaner.java` / `LogSafe.java` 两行：❌ → ✅（`common/util/llm_response_cleaner.py` / `log_safe.py`）；
- `validation/RetrievalConfigFailureAnalyzer.java` 行：❌ → ✅（`rag/retrieval/config_validation.py`）；
- §12 P2 行：`~~补框架尾款~~ ✅ **已完成**` + 交付清单 + 测试数 + 指向本计划。

- [ ] **Step 3: 收官记录（追加到本计划 §7）**

```markdown
## 7. 收官记录

> 执行于 2026-08-23，本计划 Task 1-6 全部完成（✅）。

- **Task 1-6 全部 ✅**：LogSafe（`common/util/log_safe.py`）、LLMResponseCleaner（`common/util/llm_response_cleaner.py` + json_response_parser 委托）、RedisKeySerializer（`storage/cache/key_serializer.py` + RedisCacheManager 可选前缀）、IdempotentConsume（`common/idempotent/consume.py`，sync 路径经 asyncio.run 桥接共享 cache 状态机）、检索配置校验器（`rag/retrieval/config_validation.py` + wiring 告警）、README / 对比文档销案。
- **出口测试**：全量回归 **663 passed**（基线 620 + 新增 6+12+6+9+10=43 例）。
- **已知限制**：
  1. 消费幂等为 get+set 模拟 setnx，单实例/进程内有效；跨实例原子性由 P6 real 栈 Redis 强语义兜底；
  2. sync 路径以 asyncio.run 桥接 CacheManager，仅在无运行中事件循环的线程被调用（MQ 消费者场景满足）；
  3. 检索配置校验在 wiring 为告警不阻断（保持既有装配行为）；严格阻断可由调用方 raise RetrievalConfigException；
  4. 4 处 `_CODE_FENCE` 内联清理语义更激进（会剥离中段围栏），本轮不统一，保持行为不变。
- **后续候选**：P6 real 栈（真实 Redis 原子 setnx + 消费幂等接线）、MinerU 真实 API 联调。
```

- [ ] **Step 4: 全量回归**

Run: `python -m pytest tests -q`
Expected: 全量回归通过（约 665 passed，无失败；退出码 1 仅为沙箱 `__pycache__` 写保护告警时忽略）

- [ ] **Step 5: Commit**

```bash
git add README.md docs/ragent-file-by-file-comparison.md docs/complements/p2-framework-remaining-implementation-plan.md
git commit -m "docs: close P2 framework remaining items, update README and comparison doc (P2)"
```

---

## 测试清单

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_log_safe_unit.py` | 6 | None/短文本/截断后缀/自定义 max |
| `tests/test_llm_response_cleaner_unit.py` | 8 | None/无围栏/json/裸围栏/语言标记/空白/无换行/中段围栏保留 |
| `tests/test_json_response_parser_unit.py` | 4 | 委托后解析等价回归 |
| `tests/test_redis_key_serializer_unit.py` | 6 | 序列化/反序列化/前缀/RedisCacheManager 物理键 |
| `tests/test_idempotent_consume_unit.py` | 9 | 状态枚举/async 全路径/sync 全路径/key_prefix/key_fn |
| `tests/test_retrieval_config_validator_unit.py` | 10 | 单向矛盾/大小写/collect 全量/env 驱动/异常文案 |
| **合计** | **43** | 新增用例，全量回归兜底 |

## 风险与已知限制

| 项 | 说明 |
|---|---|
| 消费幂等原子性 | CacheManager 无原子 setnx，get+set 模拟存在极小竞态窗口；与提交幂等同一 D 决策，单实例/进程内有效 |
| 检索校验不阻断 | wiring 仅告警（保持既有装配行为不变），严格模式由调用方 raise |
| 内联清理不统一 | 4 处 `_CODE_FENCE` 语义更激进（剥中段围栏），本轮保留原行为，避免回归 |
| json_response_parser 委托 | 极端非法语言标记差异由下游 `_extract_json_body` 归一化，最终解析等价 |

## 关联文档

- 对比文档：`docs/ragent-file-by-file-comparison.md`（§3 framework / infra util / validation 行、§5 缺口、§12 P2 销案）
- 本计划：`docs/complements/p2-framework-remaining-implementation-plan.md`

## 7. 收官记录（实际执行）

> 执行于 2026-08-23，本计划 Task 1-6 全部完成（✅），Subagent-Driven 执行 + 逐任务审查。

- **Task 1-6 全部 ✅**：
  - T1 LogSafe：`common/util/log_safe.py`（preview，DEFAULT_MAX=500）+ 6 例单测；
  - T2 LLMResponseCleaner：`common/util/llm_response_cleaner.py`（strip_markdown_code_fence，正则对齐 Java）+ json_response_parser 委托 + 8+4=12 例单测；
  - T3 RedisKeySerializer：`storage/cache/key_serializer.py` + RedisCacheManager 可选 `key_prefix`（默认空零行为变更）+ 6 例单测；
  - T4 IdempotentConsume：`common/idempotent/consume.py`（Status 枚举 + Guard + 装饰器，async/sync 双路径）+ 9 例单测；
  - T5 检索配置校验器：`rag/retrieval/config_validation.py`（Violation/validate/validate_env/RetrievalConfigException）+ wiring `_build_retrieval_engine` 启动期告警（不阻断）+ 10 例单测；
  - T6 文档销案：README「工具/清理」行 ✅、对比文档 framework/infra util/validation 行 + §3 + §5 + §12 P2 销案、本计划收官记录。
- **出口测试**：全量回归 **663 passed**（基线 620 + 新增 43 例，无失败；退出码 1 仅为沙箱 `__pycache__` 写保护告警）。
- **执行偏离登记**：
  1. T4 用例数按实际为 9（计划预写 11，`TestIdempotentConsumeStatus` 实际 1 例），合计新增 43 例；
  2. T4 sync 路径由计划「进程内 `_SYNC_STATES` 表」改为经 `asyncio.run` 桥接共享 cache 状态机（与 async 同源、测试要求 sync 读取 cache 状态），移除 `_SYNC_STATES`/`_SYNC_LOCK`；
  3. T6 对比文档同文件多 Edit 出现丢失更新，已逐个重应用并核对 line 50/52。
- **已知限制**：同 §7 模板（get+set 模拟 setnx 单实例、sync 桥接限定无运行事件循环线程、wiring 告警不阻断、4 处 `_CODE_FENCE` 内联不统一）。
- **后续候选**：P6 real 栈（真实 Redis 原子 setnx + 消费幂等接线）、MinerU 真实 API 联调、P2 复刻部署资源。
