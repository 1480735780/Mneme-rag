"""
storage.cache - 缓存访问抽象

    - client：CacheManager 抽象（get/set 带 TTL、JSON 序列化、异常兜底）+
      CacheCodec + MemoryCacheManager（进程内实现）+ RedisCacheManager（redis-py asyncio 真实实现，惰性加载）

对应 ragent 源码：
    - rag/core/prompt/AgentPromptCacheManager 等（StringRedisTemplate 存 JSON 字符串）
"""
from storage.cache.client import (
    CacheCodec,
    CacheManager,
    MemoryCacheManager,
    RedisCacheManager,
)

__all__ = [
    "CacheCodec",
    "CacheManager",
    "MemoryCacheManager",
    "RedisCacheManager",
]
