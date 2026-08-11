"""
core.llm.model.health_store - 模型健康状态存储（断路器模式）

本模块实现了「断路器模式（Circuit Breaker）」，用于管理各个 AI 模型实例的健康状态。
在模型路由层（RoutingExecutor）遍历候选模型时，通过本模块判断模型是否可用，
并实现「故障累积 → 熔断 → 半开探测 → 自动恢复」的完整生命周期。

对应 ragent 源码：
    com.nageoffer.ai.ragent.infra.model.ModelHealthStore

核心职责：
    1. 记录每个模型的连续失败次数
    2. 失败次数达到阈值时触发熔断（OPEN 状态），拒绝所有调用
    3. 熔断持续一段时间后自动进入半开状态（HALF_OPEN），允许一个探测请求通过
    4. 探测成功则恢复（CLOSED），探测失败则重新熔断

架构位置：
    RoutingExecutor（路由执行器）
        │
        ├─ 遍历候选 ModelTarget 列表
        ├─ is_unavailable() → 跳过不可用的模型（快速过滤）
        ├─ allow_call() → 获取调用许可（原子授牌）
        ├─ 执行 client.chat()
        ├─ mark_success() / mark_failure() → 反馈调用结果
        └─ release_half_open_permit() → 释放探测名额（finally 块）

并发安全策略：
    本模块使用 threading.RLock 保护所有状态修改操作（allow_call / mark_success / mark_failure）。
    锁内无 I/O 操作（纯内存状态机），即使运行在 asyncio 单线程事件循环中，
    也不会阻塞其他协程（因为锁内没有 await 点，协程切换只发生在 await 处）。

状态流转图：
    ┌─────────┐                      ┌─────────┐
    │ CLOSED  │ ──── 成功 ──────────▶│ CLOSED  │  (重置失败计数)
    │ (正常)  │ ◀─── 成功 ───────────│         │
    └────┬────┘                      └─────────┘
         │ 连续失败 ≥ threshold
         ▼
    ┌─────────┐                      ┌─────────┐
    │  OPEN   │ ──── 超时 ──────────▶│HALF_OPEN│  (允许一个探测请求)
    │ (熔断)  │                      │ (半开)  │
    └────┬────┘                      └────┬────┘
         │ 探测失败                        │ 探测成功
         └─────────────────────────────────┘
"""
import time
import threading
from enum import Enum
from dataclasses import dataclass
from typing import Optional

"""
    熔断器状态枚举（对应 Java 的 State 枚举）

    CLOSED:   闭合（正常工作状态）
    OPEN:     打开（熔断状态，拒绝所有调用）
    HALF_OPEN:半开（允许一个探测请求，用于验证模型是否已恢复）
    """

class HealthState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class ModelHealth:
    """
    单个模型的健康状态对象（对应 Java 的 ModelHealth 内部类）

    每个模型在 ModelHealthStore 中独立存储一份状态。
    所有字段的读写操作都在锁保护下进行，保证并发安全。

    Attributes:
        consecutive_failures: 连续失败次数（CLOSED 状态下累加，达到阈值后触发熔断）
        open_until: 熔断解除时间戳（毫秒）。0 表示未熔断；OPEN 状态下若当前时间 > open_until，则自动进入 HALF_OPEN
        half_open_in_flight: 是否正在执行探测请求。HALF_OPEN 状态下同一时刻只能有一个探测请求
        half_open_token: 当前探测请求的唯一令牌。用于 release_half_open_permit 时精确匹配，防止误释放
        state: 当前熔断器状态（CLOSED / OPEN / HALF_OPEN）
    """
    consecutive_failures: int = 0
    open_until: float = 0
    half_open_in_flight: bool = False
    half_open_token: int = 0
    state: HealthState = HealthState.CLOSED

@dataclass
class CallPermit():
    """
    模型调用许可（对应 Java 的 CallPermit record）

    由 allow_call() 方法返回，调用方持有此对象作为"入场券"。
    调用完成后，无论成功或失败，都需要传入 release_half_open_permit() 释放探测名额。

    Attributes:
        model_id: 模型唯一标识符
        half_open_token: 半开探测令牌。0 表示这是 CLOSED 状态下的正常调用；
                          >0 表示这是 HALF_OPEN 状态下的探测调用，需要配合 release_half_open_permit() 释放
    """
    model_id : str
    half_open_token: int

class ModelHealthStore():
     """
    模型健康状态存储（对应 Java 的 ModelHealthStore）

    核心功能：
        1. 为每个模型维护独立的健康状态（熔断计数器、状态机）
        2. 提供 is_unavailable() 快速过滤不可用模型（只读，无锁）
        3. 提供 allow_call() 原子性地授予调用许可（含 OPEN → HALF_OPEN 状态转换）
        4. 提供 mark_success() / mark_failure() 反馈调用结果，驱动状态机
        5. 提供 release_half_open_permit() 释放半开探测名额

    线程安全策略：
        所有状态修改方法（allow_call / mark_success / mark_failure / release_half_open_permit）
        均使用 threading.RLock 保护，保证"读取-判断-修改"整段操作的原子性。
        这对应于 Java 中 ConcurrentHashMap.compute() 的原子语义。
    """
     
     def __init__(
        self,
        failure_threshold: int,
        open_duration_ms: int
    ):
        """
        初始化模型健康状态存储

        Args:
            failure_threshold: 连续失败阈值。连续失败次数达到该值时触发熔断（进入 OPEN 状态）
            open_duration_ms: 熔断持续时间（毫秒）。熔断持续该时间后自动进入 HALF_OPEN 状态（半开探测）
        """
        # 存储每个模型的健康状态
        self.health_by_id: dict[str, ModelHealth] = {}
        # 熔断阈值
        self.failure_threshold = failure_threshold
        # 熔断持续时间
        self.open_duration_ms = open_duration_ms
        # 状态机互斥锁：对应 Java ConcurrentHashMap.compute 的按桶原子性，
        # 保证"读取-判断-修改"整段不被打断（RLock 可重入，后续 mark_* 方法复用同一把锁）
        self._lock = threading.RLock()
        # 探测令牌自增序列：对应 Java 的 AtomicLong probeTokenSeq
        self._token_seq = 0

     def _now_ms(self) -> int:
        """
        获取当前时间戳（毫秒）

        对应 Java 的 System.currentTimeMillis()。
        封装为方法便于：
            1. 统一时间基准（避免直接写 time.time() 导致单位混乱）
            2. 单元测试时可以通过 Mock 固定时间

        Returns:
            int: 当前时间的毫秒时间戳
        """
        return int(time.time() * 1000)

     def _next_token(self) -> int:
        """
        生成下一个探测令牌（自增序列）

        对应 Java 的 AtomicLong.incrementAndGet()。
        调用者必须持有 _lock，保证原子性。

        Returns:
            int: 新的自增令牌值
        """
        self._token_seq += 1
        return self._token_seq
    
     def is_unavailable(self,model_id:str):
        """
        检查模型是否不可用（只读判断，不修改状态）

        对应 Java 的 isUnavailable()。
        该方法不加锁，仅读取 health_by_id 字典（Python 中字典读取是原子操作）。
        用于 RoutingExecutor 遍历候选列表时的快速过滤，性能极高。

        判断逻辑：
            1. 如果模型从未失败过（health 不存在）→ 可用（返回 False）
            2. 如果状态是 OPEN 且熔断未到期（open_until > now）→ 不可用（返回 True）
            3. 如果状态是 HALF_OPEN 且有探测正在执行 → 不可用（返回 True）
            4. 其他情况 → 可用（返回 False）

        Args:
            model_id: 模型唯一标识符

        Returns:
            bool: True 表示模型不可用（应跳过），False 表示模型可用（可尝试调用）
        """
        health = self.health_by_id.get(model_id)  #由ID获得健康状态
        #在遍历候选ModelTarget时一直没失败，则说明可用
        if health == None:
            return False
        now_ms = self._now_ms()
        #如果健康state是OPEN且当前时间<解除熔断时间，则说明还需要熔断，模型不可用
        if health.state == HealthState.OPEN and health.open_until > now_ms:
            return True
        #如果当前状态是 HALF_OPEN，并且此时正有一个探测请求在执行,那么返回 true（不可用）；否则返回 false（可用）
        return health.state == HealthState.HALF_OPEN and health.half_open_in_flight
        
    
     def allow_call(self,model_id:str)-> Optional[CallPermit]:
        """
        原子性地授予调用许可（对应 Java 的 allowCall()）

        这是断路器状态机的核心方法，执行"读取-判断-修改"原子操作。
        使用 _lock 保证并发安全，同一时刻只有一个线程/协程能修改同一模型的状态。

        状态转换逻辑：
            CLOSED 状态：
                → 直接授予正常许可（half_open_token = 0），不修改状态

            OPEN 状态（熔断中）：
                → 检查熔断是否到期（open_until > now）
                → 未到期：返回 None（拒绝调用）
                → 已到期：转为 HALF_OPEN，授予探测许可（half_open_token > 0）

            HALF_OPEN 状态（半开）：
                → 检查是否有探测正在执行（half_open_in_flight）
                → 有探测：返回 None（拒绝并发探测）
                → 无探测：授予探测许可（half_open_token > 0）

        Args:
            model_id: 模型唯一标识符

        Returns:
            Optional[CallPermit]:
                - 返回 CallPermit：允许调用，调用完成后必须传入 release_half_open_permit()
                - 返回 None：拒绝调用（模型处于熔断中或已有探测在执行）
        """
        if model_id == None:
            return None
        now_ms = self._now_ms()
        with self._lock:  # 对应 Java 的 healthById.compute(id, ...) 原子读改写
            health = self.health_by_id.get(model_id)
            if health is None:
                health = ModelHealth()
                self.health_by_id[model_id] = health

            # ========== 状态：OPEN（熔断中） ==========
            if health.state == HealthState.OPEN:
                # 检查熔断是否到期
                if health.open_until > now_ms:
                    return None  # 未到期，拒绝

                # 熔断到期 → 转为 HALF_OPEN，授予探测许可
                health.state = HealthState.HALF_OPEN
                health.half_open_in_flight = True
                health.half_open_token = self._next_token()
                return CallPermit(model_id, health.half_open_token)

            # ========== 状态：HALF_OPEN（半开探测中） ==========
            if health.state == HealthState.HALF_OPEN:
                if health.half_open_in_flight:
                    return None  # 已有探测在执行，拒绝

                # 无探测执行 → 授予探测许可
                health.half_open_in_flight = True
                health.half_open_token = self._next_token()
                return CallPermit(model_id, health.half_open_token)

            # ========== 状态：CLOSED（正常） ==========
            # 直接授予正常许可（token = 0）
            return CallPermit(model_id, 0)

     def mark_success(self,model_id:str)-> None:
        """
        标记模型调用成功（对应 Java 的 markSuccess()）

        无论模型当前处于什么状态，调用成功后都会重置为 CLOSED：
            - 清除所有失败计数（consecutive_failures = 0）
            - 清除熔断时间（open_until = 0）
            - 清除探测标志（half_open_in_flight = False）
            - 状态设为 CLOSED

        在 HALF_OPEN 状态下调用成功，意味着模型已恢复，断路器闭合。
        在 CLOSED 状态下调用成功，重置失败计数器，保持正常工作。

        Args:
            model_id: 模型唯一标识符
        """
        with self._lock:
            health = self.health_by_id.get(model_id)
            if health is None:
                self.health_by_id[model_id] = ModelHealth()
                return
            health.state = HealthState.CLOSED
            health.consecutive_failures = 0
            health.open_until = 0
            health.half_open_in_flight = False

     def mark_failure(self,model_id:str)-> None:
        """
        标记模型调用失败（对应 Java 的 markFailure()）

        根据当前状态采取不同策略：
            HALF_OPEN 状态下的失败：
                → 探测失败，说明模型尚未恢复
                → 立即重新熔断（OPEN），不依赖失败阈值
                → open_until = now + open_duration_ms

            CLOSED 状态下的失败：
                → 累加 consecutive_failures
                → 达到 failure_threshold 时触发熔断（进入 OPEN）
                → open_until = now + open_duration_ms

        Args:
            model_id: 模型唯一标识符
        """
        with self._lock:
            now_ms = self._now_ms()
            health = self.health_by_id.get(model_id)
            if health is None:
                health = ModelHealth()
                self.health_by_id[model_id] = health

            if health.state == HealthState.HALF_OPEN:
                health.state = HealthState.OPEN
                health.open_until = now_ms + self.open_duration_ms
                health.consecutive_failures = 0
                health.half_open_in_flight = False
                return

            health.consecutive_failures += 1
            if health.consecutive_failures >= self.failure_threshold:
                health.state = HealthState.OPEN
                health.open_until = now_ms + self.open_duration_ms
                health.consecutive_failures = 0

     def release_half_open_permit(self,permit:CallPermit)-> None:
        """
        释放半开探测名额（对应 Java 的 releaseHalfOpenPermit()）

        仅在以下情况执行：
            1. permit 不为 None
            2. permit.half_open_token > 0（即这是一个探测请求，而非正常的 CLOSED 请求）

        释放条件（必须同时满足，防止误释放）：
            1. 模型当前状态是 HALF_OPEN
            2. half_open_in_flight = True（确实有探测在执行）
            3. health.half_open_token == permit.half_open_token（token 精确匹配，防止并发覆盖）

        典型用法（在 RoutingExecutor 的 finally 块中调用）：
            permit = health_store.allow_call(model_id)
            try:
                result = client.chat(...)
                health_store.mark_success(model_id)
            except Exception:
                health_store.mark_failure(model_id)
            finally:
                health_store.release_half_open_permit(permit)

        Args:
            permit: allow_call() 返回的 CallPermit 对象
        """
        if permit is None or permit.half_open_token <= 0:
            return
        with self._lock:
            health = self.health_by_id.get(permit.model_id)
            if health is None:
                return
            if (health.state == HealthState.HALF_OPEN and
                health.half_open_in_flight and
                health.half_open_token == permit.half_open_token):
                health.half_open_in_flight = False
