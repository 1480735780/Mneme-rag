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
