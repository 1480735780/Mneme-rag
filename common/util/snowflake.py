"""
雪花 ID 生成器（对应 ragent distributedid，P4 决策 D4 单机简化）

标准雪花 64 位布局：1 符号位 + 41 位毫秒时间戳 + 10 位机器位 + 12 位序列。
P4 无 Redis 分配 worker/datacenter（对齐 Java SnowflakeIdInitializer 的 Redis Lua 分配，
单机场景简化为固定 machine_id）；threading.Lock 保证并发唯一，时钟回拨抛错保护。
next_id() 返回字符串（对齐既有 DO 主键 VARCHAR(32) 语义，Java ASSIGN_ID 雪花主键）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.distributedid.SnowflakeIdInitializer（Lua 分配 worker/datacenter）
    - cn.hutool.core.lang.Snowflake（Hutool 雪花实现）
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# 位宽：机器位 10（0~1023）、序列位 12（0~4095）
_MACHINE_BITS = 10
_SEQUENCE_BITS = 12
_SEQUENCE_MASK = (1 << _SEQUENCE_BITS) - 1  # 4095
_MAX_MACHINE_ID = (1 << _MACHINE_BITS) - 1  # 1023
# 机器位与序列位合计左移量（41 位毫秒时间戳左移后拼接）
_MACHINE_SHIFT = _SEQUENCE_BITS
_TIMESTAMP_SHIFT = _MACHINE_BITS + _SEQUENCE_BITS

# 纪元（毫秒）：2024-01-01 00:00:00 UTC（对齐 Hutool 默认 epoch 可配置语义）
_DEFAULT_EPOCH_MS = 1704067200000


class SnowflakeIdGenerator:
    """
    雪花 ID 生成器（对应 Java Hutool Snowflake，P4 单机简化）

    Args:
        machine_id: 机器位（0~1023）；P4 单机默认 0，多实例部署时按实例配置
        epoch_ms:   时间戳纪元（毫秒），默认 2024-01-01
        clock:      可注入的毫秒时钟（测试用），默认 time.time()*1000
    """

    def __init__(
        self,
        machine_id: int = 0,
        epoch_ms: int = _DEFAULT_EPOCH_MS,
        clock: Optional[Callable[[], int]] = None,
    ):
        if not 0 <= machine_id <= _MAX_MACHINE_ID:
            raise ValueError(f"machine_id 必须在 0~{_MAX_MACHINE_ID} 之间，实际 {machine_id}")
        self._machine_id = machine_id
        self._epoch_ms = epoch_ms
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._last_timestamp: int = -1
        self._sequence: int = 0

    def next_id(self) -> str:
        """
        生成下一个雪花 ID（返回字符串，对齐 Java DO VARCHAR 主键）

        同一毫秒内序列自增（溢出则等待下一毫秒）；时钟回拨抛 RuntimeError 保护。
        """
        with self._lock:
            timestamp = self._current_timestamp()
            if timestamp < self._last_timestamp:
                raise RuntimeError(
                    f"时钟回拨，拒绝生成 ID：last={self._last_timestamp}, now={timestamp}"
                )
            if timestamp == self._last_timestamp:
                self._sequence = (self._sequence + 1) & _SEQUENCE_MASK
                if self._sequence == 0:
                    # 序列耗尽，自旋等待下一毫秒
                    timestamp = self._wait_next_ms(self._last_timestamp)
            else:
                self._sequence = 0
            self._last_timestamp = timestamp
            return str(self._compose(timestamp))

    # ==================== 内部 ====================

    def _current_timestamp(self) -> int:
        return self._clock()

    def _wait_next_ms(self, last_timestamp: int) -> int:
        timestamp = self._clock()
        while timestamp <= last_timestamp:
            timestamp = self._clock()
        return timestamp

    def _compose(self, timestamp: int) -> int:
        """拼接 64 位雪花 ID：41 位时间戳 << 22 | 10 位机器 << 12 | 12 位序列"""
        return (
            ((timestamp - self._epoch_ms) << _TIMESTAMP_SHIFT)
            | (self._machine_id << _MACHINE_SHIFT)
            | self._sequence
        )


# 默认单例（machine_id=0，P4 单机部署）；多实例时按配置 new SnowflakeIdGenerator(machine_id=...)
default_generator = SnowflakeIdGenerator()
