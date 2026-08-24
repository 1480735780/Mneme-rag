# -*- coding: utf-8 -*-
"""
缓存访问抽象 + 进程内/Redis 实现（对应 Java StringRedisTemplate / RedissonClient 的字符串缓存用法）

storage/cache 是 C 层「外部设施」的公共底座之一：给 rag/ 各子包提供
「读 / 写（带 TTL）+ 删除」的缓存边界，JSON 序列化、异常兜底统一收口在
实现内完成，消费方（AgentPromptCacheManager、QueryTermMappingCacheManager、
IntentTreeCacheManager …）面向 CacheManager 抽象编程，不感知介质差异。

接口对齐 Java 侧缓存管理器的共同用法（StringRedisTemplate 存 JSON 字符串）：
    - CacheManager.get     → 读 JSON 字符串并反序列化；未命中 / 已过期 / 异常 → None
    - CacheManager.set     → JSON 序列化后写入并设 TTL；异常 → False
    - CacheManager.delete  → 删除；异常 → False

MVP 阶段默认以 MemoryCacheManager（进程内 dict + 单调时钟过期）兜底，不接真实 Redis；
真实实现 RedisCacheManager 是项目中唯一依赖 redis-py 的一处，构造时惰性加载依赖，
捕获 redis.exceptions.RedisError / ConnectionError 兜底返回 None / False（对齐 Java 的
catch(Exception) 语义）。消费方无感知介质差异，见「5.0 步骤 2」。

对应 ragent 源码：
    - rag/core/prompt/AgentPromptCacheManager（StringRedisTemplate + ObjectMapper）
    - rag/core/rewrite/QueryTermMappingCacheManager / rag/core/intent/IntentTreeCacheManager
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple


class CacheCodec:
    """JSON 序列化编解码（对齐 Java ObjectMapper 的 JSON 字符串存取）"""

    def serialize(self, value: Any) -> str:
        """序列化为 JSON 字符串；不支持的取值类型抛 TypeError / ValueError"""
        return json.dumps(value, ensure_ascii=False)

    def deserialize(self, raw: str) -> Any:
        """反序列化 JSON 字符串；格式非法抛 ValueError"""
        return json.loads(raw)


class CacheManager(ABC):
    """缓存访问抽象：读 / 写（带 TTL）/ 删除，JSON 序列化与异常兜底在实现内收口"""

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """
        读取并反序列化

        Args:
            key: 缓存键

        Returns:
            Optional[Any]: 命中返回反序列化值；未命中 / 已过期 / 反序列化失败 / 后端异常 → None
        """
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> bool:
        """
        序列化后写入并设置过期时间

        Args:
            key:   缓存键
            value: 任意 JSON 可序列化对象
            ttl:   过期秒数（整数秒）；None = 不设过期；<=0 视为非法参数

        Returns:
            bool: 写入成功返回 True；序列化失败 / TTL 非法 / 后端异常 → False
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        删除键

        Returns:
            bool: 键存在且删除成功返回 True；键不存在 / 后端异常 → False
        """
        ...


class MemoryCacheManager(CacheManager):
    """
    进程内缓存实现：dict + 单调时钟过期（对齐 Redis 的过期语义，读时惰性清除）

    Args:
        codec: 序列化编解码器，默认 JSON
    """

    def __init__(self, codec: Optional[CacheCodec] = None):
        self._codec = codec or CacheCodec()
        self._store: Dict[str, Tuple[str, Optional[float]]] = {}

    async def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        raw, expires_at = entry
        if expires_at is not None and time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        try:
            return self._codec.deserialize(raw)
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> bool:
        if ttl is not None and ttl <= 0:
            return False
        try:
            raw = self._codec.serialize(value)
        except Exception:
            return False
        expires_at = None if ttl is None else time.monotonic() + ttl
        self._store[key] = (raw, expires_at)
        return True

    async def delete(self, key: str) -> bool:
        if key in self._store:
            self._store.pop(key, None)
            return True
        return False

    def size(self) -> int:
        """当前缓存条目数（测试 / 诊断用）"""
        return len(self._store)


class RedisCacheManager(CacheManager):
    """
    真实 Redis 实现（redis-py asyncio）——项目中唯一依赖 redis-py 的一处

    Args:
        redis: redis.asyncio.Redis 客户端实例（必须注入，连接串等经配置/环境变量）
        codec: 序列化编解码器，默认 JSON
        key_prefix: 物理 Redis 键前缀（经 RedisKeySerializer 统一加前缀，默认空 = 行为不变）
    """

    def __init__(
        self,
        redis: Any = None,
        codec: Optional[CacheCodec] = None,
        key_prefix: str = "",
    ):
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

    async def get(self, key: str) -> Optional[Any]:
        try:
            raw = await self._redis.get(self._real_key(key))
        except (self._redis_error, self._connection_error):
            return None
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        try:
            return self._codec.deserialize(raw)
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> bool:
        if ttl is not None and ttl <= 0:
            return False
        try:
            raw = self._codec.serialize(value)
        except Exception:
            return False
        try:
            if ttl is None:
                await self._redis.set(self._real_key(key), raw)
            else:
                # EX 仅支持整秒；TTL 已在上面排除 <=0，int() 后仍 >0 才合法
                await self._redis.set(self._real_key(key), raw, ex=int(ttl))
        except (self._redis_error, self._connection_error, ValueError):
            return False
        return True

    async def delete(self, key: str) -> bool:
        try:
            result = await self._redis.delete(self._real_key(key))
            return bool(result)
        except (self._redis_error, self._connection_error):
            return False

    def _real_key(self, key: str) -> str:
        """物理 Redis 键：经 RedisKeySerializer 加前缀（空前缀 = 原键，行为不变）"""
        return self._key_serializer.serialize(key).decode("utf-8")
