# -*- coding: utf-8 -*-
"""
knowledge.versioning.lease - 心跳租约（P9 §7 / §17.4.3）

设计约束（冻结）：
    - 心跳使用独立线程和独立数据库连接，不能依赖共享事件循环——事件循环被 embedding
      调用阻塞时心跳即停，fencing 失效（§17.4.3）；本类用 threading.Thread，
      dao_factory 由调用方提供（PG 侧必须返回独立连接的 dao，见 app 装配）；
    - 心跳 UPDATE 返回 0 行 = fencing 丢失（token 不符 / 租约已过期被接管）→
      lost 置位，worker 必须**立即停止：不清理、不发布**（§7）；其余 worker 侧动作前
      用 check() 强制复核；
    - 网络异常 ≠ fencing 丢失：租约有效期（默认 5 分钟）远大于心跳间隔（默认 60 秒），
      瞬时抖动允许重试；但自上次续约成功起超过租约有效期仍失败 → 主动置位（服务端
      视角租约必已过期，继续工作只会留下孤儿数据）；
    - 时间以数据库时间为准（续约 SQL 用 NOW()，§17.4.2；InMemory 单进程用客户端时钟）。
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class LeaseLost(RuntimeError):
    """租约 fencing 丢失：当前 worker 必须立即停止，不清理、不发布（§7）"""


class HeartbeatLease:
    """
    心跳续约线程（一个 worker 生命周期一个实例）

    Args:
        job_id:        任务 id
        lease_token:   领取时授予的 fencing token
        dao_factory:   返回 UpdateJobDao 的工厂——PG 侧必须每次（或每 worker）提供
                       独立连接的 dao（独立 engine/连接，不与业务共享池）；InMemory
                       可返回共享 client 上的 dao（RLock 线程安全）
        lease_seconds: 租约时长（默认 300）
        heartbeat_seconds: 心跳间隔（默认 60；成功续约后重置为满额）
    """

    def __init__(
        self,
        job_id: str,
        lease_token: str,
        *,
        dao_factory: Callable,
        lease_seconds: int = 300,
        heartbeat_seconds: int = 60,
    ):
        self.job_id = job_id
        self.lease_token = lease_token
        self._dao_factory = dao_factory
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._lost = threading.Event()
        self._stopped = threading.Event()
        self._last_ok = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name=f"hb-{job_id[:8]}", daemon=True
        )

    # ── 生命周期 ──

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """停止心跳（worker 正常收尾；不改变 lost 状态）"""
        self._stopped.set()
        self._thread.join(timeout=self._heartbeat_seconds + 5)

    # ── 状态 ──

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def check(self) -> None:
        """fencing 复查：丢失则抛 LeaseLost（worker 在关键动作前调用：发布前/清理前/
        长构建的分批循环内均可调用，实现「立即停止」的协作式中止）"""
        if self._lost.is_set():
            raise LeaseLost(f"job={self.job_id} 租约 fencing 丢失，worker 立即停止")

    # ── 心跳线程体 ──

    def _run(self) -> None:
        # 首跳前等待一个心跳间隔（claim 时已授予满额租约）
        while not self._stopped.wait(self._heartbeat_seconds):
            if self._renew_once():
                self._last_ok = time.monotonic()
                continue
            self._lost.set()
            return
        # 正常停止：若已丢失（stop 与丢失竞争），保留 lost 语义
        if time.monotonic() - self._last_ok > self._lease_seconds:
            self._lost.set()

    def _renew_once(self) -> bool:
        """单次续约：0 行 = fencing 丢失；异常 = 瞬时抖动（超租约有效期由调用侧判丢失）"""
        try:
            dao = self._dao_factory()
            renewed = dao.renew_lease(self.job_id, self.lease_token, self._lease_seconds)
        except Exception:  # noqa: BLE001 - 心跳线程内吞异常：网络抖动重试，超时由 _run 判定
            return time.monotonic() - self._last_ok <= self._lease_seconds
        return bool(renewed)
