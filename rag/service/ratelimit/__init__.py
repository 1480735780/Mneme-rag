# -*- coding: utf-8 -*-
"""
rag.service.ratelimit - M6 聊天并发限流子包

    config      限流配置（RateLimitProperties，对应 Java RAGRateLimitProperties）
    fair_rate_limiter  进程内公平限流器（6.2） / Redis 分布式（6.3，可选）
    chat_queue_limiter 聊天排队入口（6.4）
"""
from rag.service.ratelimit.config import RateLimitProperties
from rag.service.ratelimit.chat_queue_limiter import ChatQueueLimiter
from rag.service.ratelimit.fair_rate_limiter import (
    FairRateLimiter,
    Permit,
    ProcessFairRateLimiter,
    RateLimitError,
    RateLimitTimeout,
    RedisFairRateLimiter,
    RedisPermit,
)

__all__ = [
    "RateLimitProperties",
    "ChatQueueLimiter",
    "FairRateLimiter",
    "Permit",
    "ProcessFairRateLimiter",
    "RateLimitError",
    "RateLimitTimeout",
    "RedisFairRateLimiter",
    "RedisPermit",
]