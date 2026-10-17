# -*- coding: utf-8 -*-
"""P10.5 T5 单测：t_llm_call 保留清理 sweeper（决策 D14）+ wiring 装配 + /metrics 区块

覆盖：
    - 配置：缺省全默认（180 天 / 5k/批 / 6h / dry-run=true）；校验即拒绝（未知键/
      类型错/非法值）；空文件 = 全默认
    - dry-run（首跑安全模式）：只统计将删行数 + 最早行 create_time，绝不动数据
    - 真删：分块推进（batch_size 控制批数）直至无候删；批间让锁；统计累计
    - 切换真删的边界：create_time 恰等于 cutoff 不删（严格 lt）
    - 放法纪律：cutoff 计算/统计/删除三类异常全吞掉计数（sweep_errors/delete_errors），
      绝不上抛
    - 调度：start/stop 幂等、interval 打断、停后不再扫描
    - wiring：出厂样例 → dry_run=true 装配（默认路径语义）；非法 yaml → 降级缺席；
    - /metrics：retention 区块 app 级可见（sweeper 缺席则不挂账）
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from core.llm.observation import LLM_CALL_TABLE, LlmCallDao
from core.llm.observation.sweeper import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DAYS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_DRY_RUN,
    RetentionConfig,
    RetentionConfigError,
    RetentionSweeperJob,
    load_retention_config,
)
from fastapi.testclient import TestClient
from storage.database import InMemoryDatabaseClient

# 固定「当前时刻」：naive NOW（房式约定；种子行由 NOW-偏移推导，时区无关）
NOW = datetime(2026, 10, 17, 12, 0, 0)

_PRICED = json.dumps({"pricing_version": "v1", "status": "priced"})


def _dao(*, ensure: bool = True) -> LlmCallDao:
    db = InMemoryDatabaseClient()
    if ensure:
        from storage.database.schema import DEFAULT_TABLES

        db.ensure_schema(DEFAULT_TABLES)
    return LlmCallDao(db)


def _row(request_uid: str, created: datetime, model: str = "gpt-x") -> dict:
    return {
        "request_uid": request_uid,
        "call_kind": "chat",
        "provider": "p",
        "model": model,
        "status": "success",
        "scene": "chat",
        "purpose": "generate",
        "tier": "standard",
        "attempt_no": 1,
        "is_final": 1,
        "token_source": "real",
        "pricing_snapshot": _PRICED,
        "create_time": created.isoformat(),
    }


def _seed_expired_and_fresh(dao: LlmCallDao, *, expired: int = 2, fresh: int = 2) -> None:
    """种子：expired 行（300d/181d 前，早于 cutoff=NOW-180d → 候删）；
    fresh 行（179d/1d 前，窗口内 → 保留）"""
    expired_off = [timedelta(days=300), timedelta(days=181)]
    fresh_off = [timedelta(days=179), timedelta(days=1)]
    for i in range(expired):
        dao.insert(_row(f"rq-old-{i}", NOW - expired_off[i]))
    for i in range(fresh):
        dao.insert(_row(f"rq-new-{i}", NOW - fresh_off[i]))


def _job(dao: LlmCallDao, **kw) -> RetentionSweeperJob:
    job_kw = {key: kw.pop(key) for key in tuple(kw) if key in ("batch_yield_seconds",)}
    defaults = dict(days=180, batch_size=5000, interval_seconds=21600.0, dry_run=True)
    defaults.update(kw)
    return RetentionSweeperJob(
        dao, RetentionConfig(**defaults), now_fn=lambda: NOW, **job_kw
    )


# ==================== 配置（校验即拒绝） ====================


def _write_yaml(tmp_path: Path, text: str) -> str:
    path = tmp_path / "retention.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_config_defaults_and_overrides(tmp_path):
    cfg = load_retention_config(_write_yaml(tmp_path, ""))
    assert cfg == RetentionConfig()  # 空文件 = 全默认（缺省即诚实起点）

    cfg = load_retention_config(_write_yaml(tmp_path, (
        "retention:\n"
        "  days: 90\n"
        "  batch_size: 1000\n"
        "  interval_seconds: 3600\n"
        "  dry_run: false\n"
    )))
    assert (cfg.days, cfg.batch_size, cfg.interval_seconds, cfg.dry_run) == \
        (90, 1000, 3600.0, False)

    assert DEFAULT_DAYS == 180 and DEFAULT_BATCH_SIZE == 5000
    assert DEFAULT_INTERVAL_SECONDS == 21600.0 and DEFAULT_DRY_RUN is True


def test_config_rejects_invalid(tmp_path):
    cases = [
        ("alerts:\n  days: 180\n", "retention"),                      # 顶层未知键
        ("retention:\n  days: 180\n  bogus: 1\n", "bogus"),           # 内层未知键
        ("retention:\n  days: 0\n", "days"),                          # days<1
        ("retention:\n  days: '180'\n", "days"),                      # 类型错
        ("retention:\n  batch_size: -5\n", "batch_size"),
        ("retention:\n  interval_seconds: 0\n", "interval_seconds"),
        ("retention:\n  dry_run: 'yes'\n", "dry_run"),                # 非布尔
        ("retention: [1, 2]\n", "retention"),                         # 非映射
    ]
    for text, match in cases:
        with pytest.raises(RetentionConfigError, match=match):
            load_retention_config(_write_yaml(tmp_path, text))


def test_config_missing_file_raises():
    with pytest.raises(Exception):
        load_retention_config("definitely_missing_dir/retention.yaml")


# ==================== dry-run（首跑安全模式，D14） ====================


def test_sweep_dry_run_counts_without_deleting():
    dao = _dao()
    _seed_expired_and_fresh(dao)
    job = _job(dao)

    asyncio.run(job.sweep())

    assert job.stats.dry_runs == 1 and job.stats.deleted_rows == 0
    assert job.stats.would_delete == 2
    # 最早行日期（dry-run 对账输出 D14）：NOW-300d 那行
    assert job.stats.earliest_create_time == (NOW - timedelta(days=300)).isoformat()
    assert job.metrics()["dry_run"] is True
    assert len(dao._db._tables[LLM_CALL_TABLE]) == 4  # 一行未动


def test_sweep_dry_run_empty_table():
    job = _job(_dao())
    asyncio.run(job.sweep())
    assert job.stats.would_delete == 0
    assert job.stats.earliest_create_time is None


# ==================== 真删（分块 + 让锁 + 边界） ====================


def test_sweep_delete_blocked_until_dry_run_flipped_off():
    """首跑 dry-run 不动数据；把 dry_run 置 false 后真删（人工核对承诺 8 流程语义）"""
    dao = _dao()
    _seed_expired_and_fresh(dao)
    job = _job(dao)
    asyncio.run(job.sweep())
    assert len(dao._db._tables[LLM_CALL_TABLE]) == 4

    wet = _job(dao, dry_run=False)
    asyncio.run(wet.sweep())
    assert wet.stats.deleted_rows == 2 and wet.stats.delete_errors == 0
    remaining = dao._db._tables[LLM_CALL_TABLE]
    assert len(remaining) == 2
    assert all(row["request_uid"].startswith("rq-new-") for row in remaining)


def test_sweep_delete_chunked_by_batch_size():
    dao = _dao()
    _seed_expired_and_fresh(dao)                      # 2 行候选 + 2 行保留
    job = _job(dao, dry_run=False, batch_size=1, batch_yield_seconds=0.01)
    asyncio.run(job.sweep())
    assert job.stats.deleted_rows == 2
    assert job.metrics()["deleted_rows"] == 2


def test_sweep_delete_respects_cutoff_boundary():
    """create_time 恰等于 cutoff（NOW-180d）不删（严格 lt 语义）"""
    dao = _dao()
    dao.insert(_row("rq-boundary", NOW - timedelta(days=180)))
    job = _job(dao, dry_run=False)
    asyncio.run(job.sweep())
    assert job.stats.deleted_rows == 0
    assert len(dao._db._tables[LLM_CALL_TABLE]) == 1


def test_sweep_delete_nothing_old_short_circuits():
    dao = _dao()
    _seed_expired_and_fresh(dao, expired=0, fresh=2)
    job = _job(dao, dry_run=False)
    asyncio.run(job.sweep())
    assert job.stats.deleted_rows == 0
    assert job.stats.delete_errors == 0


# ==================== 异常吞掉计数（观测清理永不打挂进程） ====================


def test_delete_error_swallowed_and_counted():
    class _BurstDao:
        def delete_chunk_created_before(self, *_a, **_kw):
            raise RuntimeError("db gone")

    job = RetentionSweeperJob(
        _BurstDao(), RetentionConfig(dry_run=False), now_fn=lambda: NOW
    )
    asyncio.run(job.sweep())
    assert job.stats.delete_errors == 1
    assert job.stats.deleted_rows == 0


def test_dry_run_stats_error_swallowed_and_counted():
    class _BurstDao:
        def count_created_before(self, *_a, **_kw):
            raise RuntimeError("count boom")

    job = RetentionSweeperJob(
        _BurstDao(), RetentionConfig(dry_run=True), now_fn=lambda: NOW
    )
    asyncio.run(job.sweep())
    assert job.stats.sweep_errors == 1
    assert job.stats.dry_runs == 0


def test_metrics_snapshot_never_raises():
    job = _job(_dao())
    assert job.metrics()["sweeps"] == 0  # 未扫描时也可见且可用
    async def scenario():
        await job.sweep()   # 已跑 → last_sweep_at 置位
        assert job.metrics()["last_sweep_at"] is not None
        assert job.metrics()["days"] == 180
    asyncio.run(scenario())


# ==================== 调度（lifespan 先例） ====================


def test_sweeper_start_stop_lifecycle():
    sweeps = {"n": 0}

    job = _job(_dao(), dry_run=True, interval_seconds=0.02)
    raw_sweep = job.sweep

    def _counting_sweep():
        sweeps["n"] += 1
        return raw_sweep()

    job.sweep = _counting_sweep  # 测试注入计数（不改调度面）

    async def scenario():
        await job.start()
        await job.start()  # 幂等：不得起两个扫描协程
        await asyncio.sleep(0.2)
        await job.stop()
        after_stop = sweeps["n"]

        await asyncio.sleep(0.1)
        assert sweeps["n"] == after_stop  # 停后不再扫描
        await job.stop()  # 幂等停机
        assert sweeps["n"] >= 2

    asyncio.run(scenario())


# ==================== wiring 与 /metrics ====================


def test_wiring_sample_yaml_assembles_dry_run_sweeper():
    """出厂样例（默认路径语义）→ sweeper 装配且 dry_run=true（承诺 8 的安全起点）"""
    container = AppContainer._build_memory(AppSettings())
    try:
        job = container.retention_sweeper
        assert job is not None
        assert container.retention_config.days == 180
        assert container.retention_config.dry_run is True
        assert job.metrics()["batch_size"] == 5000
    finally:
        asyncio.run(container.aclose())


def test_wiring_bad_yaml_degrades_to_none(tmp_path):
    cfg_path = _write_yaml(tmp_path, "retention:\n  days: 0\n")
    container = AppContainer._build_memory(AppSettings(retention_yaml_path=cfg_path))
    try:
        assert container.retention_sweeper is None
        assert container.retention_config is None  # 配置产物同步缺席
        # 清理缺席不影响其余观测装配（hub 三消费者照常）
        assert container.observation_hub is not None
    finally:
        asyncio.run(container.aclose())


def test_wiring_retention_yaml_env_override(tmp_path):
    cfg_path = _write_yaml(tmp_path, (
        "retention:\n  days: 30\n  batch_size: 100\n  interval_seconds: 60\n"
        "  dry_run: false\n"
    ))
    container = AppContainer._build_memory(AppSettings(retention_yaml_path=cfg_path))
    try:
        assert container.retention_sweeper is not None
        assert container.retention_config.days == 30
        assert container.retention_config.dry_run is False
    finally:
        asyncio.run(container.aclose())


def test_metrics_retention_block_visible_app_level():
    app = create_app(AppSettings(stack_profile="memory"))
    with TestClient(app) as client:
        data = client.get("/metrics").json()["data"]
        retention = data["retention"]
        assert retention["days"] == 180
        assert retention["dry_run"] is True
        # 出厂样例为安全模式：即便 lifespan 已将定时器起起来，也绝不真删
        assert retention["deleted_rows"] == 0
        # lifespan 已启动定时协程（任务存活；首跳在 interval 到期才发生）
        job = client.app.state.container.retention_sweeper
        assert job is not None and job._task is not None and not job._task.done()


def test_metrics_retention_block_absent_when_sweeper_missing(tmp_path):
    cfg_path = _write_yaml(tmp_path, "retention:\n  days: 0\n")
    app = create_app(AppSettings(stack_profile="memory", retention_yaml_path=cfg_path))
    with TestClient(app) as client:
        data = client.get("/metrics").json()["data"]
        assert "retention" not in data  # sweeper 缺席不挂账（不出空键）
