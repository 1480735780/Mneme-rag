# -*- coding: utf-8 -*-
"""
core.llm.observation.sweeper - t_llm_call 保留清理 sweeper（P10.5 T5，决策 D14）

- **默认保留 180 天**（yaml 可配 days/batch_size/interval_seconds/dry_run），按
  create_time 物理删除；**分块删除（默认 5k/批、批间让锁）**——防长事务拖慢在线写；
- **首跑必须 dry-run**：只统计「将删行数 + 最早行 create_time」不动数据，人工核对
  日志输出后把 retention.dry_run 置 false 才真删（承诺 8 的保留期兑现点；P9 阶段 6 模式）；
- 删除入口收敛在 LlmCallDao 三个专用方法（本模块唯一删除特例，其他调用方仍无删除路径）；
- 调度纪律：复用 lifespan start/stop 模式（knowledge_schedule_job / BudgetAlertPollJob
  同款）；删除/统计经 asyncio.to_thread 出事件循环（DAO 线程安全先例）；
- 戒律：全部异常吞掉计数（sweep_errors/delete_errors 进 /metrics 的 retention 区块）——
  观测清理的故障永不打挂进程。

对应 P10 计划：docs/rag/p10-observability-operations-implementation-plan.md §2 D14、
P10.5 任务行（T5）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

# yaml 出厂默认（D14 冻结口径；sample 与缺省键一致）
DEFAULT_DAYS = 180
DEFAULT_BATCH_SIZE = 5000
DEFAULT_INTERVAL_SECONDS = 21600.0   # 默认 6h 一扫（低频后台任务）
DEFAULT_DRY_RUN = True

# 切换真删后批间让锁（秒）——让锁不是睡眠阈值而是礼让点：给在线写让出锁窗口
BATCH_YIELD_SECONDS = 0.05


class RetentionConfigError(ValueError):
    """保留清理配置非法（校验即拒绝；wiring 捕获后降级不装配）"""


@dataclass(frozen=True)
class RetentionConfig:
    """保留清理配置（frozen：装配期一次性解析，运行期只读）"""

    days: int = DEFAULT_DAYS                # 保留天数；create_time < now - days 即候删
    batch_size: int = DEFAULT_BATCH_SIZE    # 分块删除批量（批间让锁）
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    dry_run: bool = DEFAULT_DRY_RUN         # 首跑必须 dry-run；人工核对后置 false


_KNOWN_KEYS = {"days", "batch_size", "interval_seconds", "dry_run"}


def load_retention_config(path: str) -> RetentionConfig:
    """加载 retention yaml（alerts 同款校验即拒绝：未知键/类型错/非法值全部拒绝）

    覆盖语义：文件缺失/顶层非 retention 映射/键类型错/值非法（days<1、batch_size<1、
    interval_seconds<=0、dry_run 非布尔）都比照配置不可用——wiring 侧降级不装配。
    """
    with open(path, encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh)

    if data is None:
        return RetentionConfig()  # 空文件 = 全默认（缺省即诚实起点）
    if not isinstance(data, dict) or set(data.keys()) - {"retention"}:
        raise RetentionConfigError("顶层仅允许 retention 一个键")
    inner = data["retention"]
    if not isinstance(inner, dict):
        raise RetentionConfigError("retention 必须是映射")
    unknown = set(inner.keys()) - _KNOWN_KEYS
    if unknown:
        raise RetentionConfigError(f"retention 含未知键：{sorted(unknown)}")

    cfg = RetentionConfig()
    if "days" in inner:
        days = inner["days"]
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise RetentionConfigError("days 必须是 >=1 的整数")
        cfg = replace(cfg, days=days)
    if "batch_size" in inner:
        batch = inner["batch_size"]
        if not isinstance(batch, int) or isinstance(batch, bool) or batch < 1:
            raise RetentionConfigError("batch_size 必须是 >=1 的整数")
        cfg = replace(cfg, batch_size=batch)
    if "interval_seconds" in inner:
        interval = inner["interval_seconds"]
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
            raise RetentionConfigError("interval_seconds 必须是 >0 的数值")
        cfg = replace(cfg, interval_seconds=float(interval))
    if "dry_run" in inner:
        dry_run = inner["dry_run"]
        if not isinstance(dry_run, bool):
            raise RetentionConfigError("dry_run 必须是布尔")
        cfg = replace(cfg, dry_run=dry_run)
    return cfg


@dataclass
class RetentionStats:
    """保留清理语义计数（D13 可观测；经 metrics() 进 /metrics retention 区块）"""

    sweeps: int = 0                  # 已执行扫描数（含 dry-run）
    sweep_errors: int = 0            # cutoff 计算/统计阶段异常
    dry_runs: int = 0                # dry-run 扫描数
    would_delete: int = 0            # 最近一次 dry-run 统计的将删行数
    earliest_create_time: Optional[str] = None  # 最近一次扫描时的全表最早行（ISO）
    deleted_rows: int = 0            # 真删累计行数
    delete_errors: int = 0           # 真删阶段某批失败（中断本轮，下轮重试）
    last_sweep_at: Optional[str] = None  # 最近一次扫描 ISO（D13 最近触发）

    def snapshot(self) -> Dict[str, Any]:
        return {
            "sweeps": self.sweeps,
            "sweep_errors": self.sweep_errors,
            "dry_runs": self.dry_runs,
            "would_delete": self.would_delete,
            "earliest_create_time": self.earliest_create_time,
            "deleted_rows": self.deleted_rows,
            "delete_errors": self.delete_errors,
            "last_sweep_at": self.last_sweep_at,
        }


class RetentionSweeperJob:
    """保留清理定时任务：dry-run 统计 / 分块真删（D14；异常吞掉计数不上抛）"""

    def __init__(
        self,
        dao: Any,  # LlmCallDao（core 内环依赖，避免运行期循环 import 用鸭子类型）
        config: RetentionConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        now_fn: Callable[[], datetime] = datetime.now,
        batch_yield_seconds: float = BATCH_YIELD_SECONDS,
    ):
        self._dao = dao
        self.config = config
        self._clock = clock
        self._now_fn = now_fn
        self._batch_yield = batch_yield_seconds
        self.stats = RetentionStats()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    # ==================== 调度（lifespan 调用；PollJob 同款先例） ====================

    async def start(self) -> None:
        """启动后台扫描协程（幂等；事件循环内调用——lifespan 启动点）"""
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止协程（不 drain——清理扫描是可重入幂等动作，中断即本轮作废）"""
        self._stopping = True
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # 正常停机路径
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.wait_for(_sleep_tick(), timeout=self.config.interval_seconds)
            except asyncio.TimeoutError:
                pass  # 到期：执行一跳扫描
            if self._stopping:
                return
            await self.sweep()

    async def sweep(self) -> None:
        """执行一轮扫描（dry-run 只统计；真删分块推进直至无候删）"""
        self.stats.sweeps += 1
        self.stats.last_sweep_at = self._now_fn().isoformat(timespec="seconds")
        try:
            cutoff = (self._now_fn() - timedelta(days=self.config.days)).isoformat()
        except Exception:  # noqa: BLE001 —— cutoff 计算异常吞掉计数
            self.stats.sweep_errors += 1
            return

        if self.config.dry_run:
            await self._sweep_dry_run(cutoff)
        else:
            await self._sweep_delete(cutoff)

    async def _sweep_dry_run(self, cutoff: str) -> None:
        """首跑安全模式：只统计将删行数与最早行日期，不动一行数据"""
        try:
            would_delete, earliest = await asyncio.to_thread(self._read_stats, cutoff)
        except Exception as exc:  # noqa: BLE001 —— 统计异常吞掉计数（清理故障不上抛）
            self.stats.sweep_errors += 1
            logger.warning("retention dry-run 统计失败（%s: %s）", type(exc).__name__, exc)
            return
        self.stats.dry_runs += 1
        self.stats.would_delete = would_delete
        self.stats.earliest_create_time = earliest
        logger.info(
            "retention dry-run：将删 %s 行（最早行 create_time=%s；保留 %s 天，cutoff=%s）"
            "——人工核对后把 retention.dry_run 置 false 启用真删",
            would_delete, earliest or "（空表）", self.config.days,
            cutoff[:19].replace("T", " "),
        )

    def _read_stats(self, cutoff: str) -> "tuple[int, Optional[str]]":
        return (
            self._dao.count_created_before(cutoff),
            self._dao.earliest_create_time(),
        )

    async def _sweep_delete(self, cutoff: str) -> None:
        """真删模式：分块删除直至无候删（批间让锁；某批失败中断本轮、下轮重试）"""
        total, batches = 0, 0
        while not self._stopping:
            try:
                deleted = await asyncio.to_thread(
                    self._dao.delete_chunk_created_before, cutoff, self.config.batch_size
                )
            except Exception as exc:  # noqa: BLE001 —— 删除异常吞掉计数并中断本轮
                self.stats.delete_errors += 1
                logger.warning(
                    "retention 真删第 %s 批失败（已删 %s 行；%s: %s）——下轮重试",
                    batches + 1, total, type(exc).__name__, exc,
                )
                return
            total += deleted
            batches += 1
            if deleted < self.config.batch_size:
                break
            await asyncio.sleep(self._batch_yield)  # 批间让锁（防长事务拖慢在线写）
        self.stats.deleted_rows += total
        logger.info(
            "retention 真删完成：本轮删 %s 行 / %s 批（保留 %s 天，cutoff=%s）",
            total, batches, self.config.days, cutoff[:19].replace("T", " "),
        )

    # ==================== 序列化出口（D13） ====================

    def metrics(self) -> Dict[str, Any]:
        """/metrics 的 retention 区块（语义计数 + 配置回显；不得 5xx）"""
        try:
            snapshot = self.stats.snapshot()
        except Exception:  # noqa: BLE001 —— 快照异常退化为最小可见面
            snapshot = {"sweeps": 0, "snapshot_error": True}
        return {
            "days": self.config.days,
            "batch_size": self.config.batch_size,
            "interval_seconds": self.config.interval_seconds,
            "dry_run": self.config.dry_run,
            **snapshot,
        }


async def _sleep_tick() -> None:
    """可停的长睡眠点（外层 wait_for 按 interval 打断——定时与停机双响应）"""
    await asyncio.sleep(3600.0)
