# -*- coding: utf-8 -*-
"""
智能体提示词解析器的 DB 数据源实现 + Redis 版缓存管理器
（对应 Java AgentPromptResolver 的 DB 部分 + AgentPromptCacheManager 的 Redis 部分）

职责划分：
    - RedisAgentPromptCacheManager：解析结果缓存（TTL 1 小时）。经 5.0 CacheManager 抽象存取
      （生产注入 RedisCacheManager，未注入时进程内 MemoryCacheManager 兜底），JSON 序列化与
      Redis 异常兜底由 CacheManager 收口；本层再兜一层桥接异常，语义对齐 Java：
      读失败返回 None（回源 DB）、写/删失败仅告警不抛错。
    - DatabaseAgentPromptResolver：面向生产的解析器。数据源为 agent_profile（内置/激活标记）+
      agent_prompt（agentId + slotKey + content）两张表，叠加回落规则：
      先铺内置智能体（builtin=1）作基线，再让激活智能体（active=1）的非空槽位覆盖，
      空白内容不参与覆盖，以此实现回落；多实例按 createTime、id 升序取第一条。

同步/异步桥接说明：AgentPromptResolver 抽象（A 层）为同步接口，被 RAGPromptService /
RAGChatEngine 在异步链路中同步调用；而 5.0 CacheManager 是 asyncio 接口。
_AsyncCacheBridge 以私有事件循环线程承载协程并阻塞等待结果，对应 Java
StringRedisTemplate 在请求线程内的阻塞语义。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.prompt.AgentPromptResolver（loadFromDb / firstByFlag /
      putNonBlank / loadOwnPrompts）
    - com.nageoffer.ai.ragent.rag.core.prompt.AgentPromptCacheManager（StringRedisTemplate +
      ObjectMapper，key ragent:agent:resolved-prompts，TTL 1 小时）
    - com.nageoffer.ai.ragent.rag.dao.entity.AgentProfileDO / AgentPromptDO（逻辑删除 deleted）
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from rag.prompt.builder import (
    AgentPromptCacheManager,
    AgentPromptResolver,
    AgentPromptSlot,
)
from rag.prompt.formatter import PromptTemplateUtils
from storage.cache import CacheManager, MemoryCacheManager
from storage.cache.bridge import AsyncCacheBridge as _AsyncCacheBridge
from storage.database.client import Condition, DatabaseClient

logger = logging.getLogger(__name__)

# 表名（对齐 Java DO @TableName）
AGENT_PROFILE_TABLE = "t_agent_profile"
AGENT_PROMPT_TABLE = "t_agent_prompt"

# 缓存 key 与 TTL（对齐 Java AgentPromptCacheManager 常量：1 小时过期）
AGENT_PROMPT_CACHE_KEY = "ragent:agent:resolved-prompts"
AGENT_PROMPT_CACHE_TTL_SECONDS = 3600.0


class RedisAgentPromptCacheManager(AgentPromptCacheManager):
    """
    Redis 版智能体提示词缓存管理器（对应 Java AgentPromptCacheManager）

    缓存的是激活智能体叠加自定义提示词之后的结果，命中即可直接取用。
    任何智能体或槽位写操作后必须 clear_cache()，否则改动直到过期才生效。

    Args:
        cache_manager: 5.0 缓存抽象实例（生产注入 RedisCacheManager；默认进程内 MemoryCacheManager）
        cache_key:     缓存键
        ttl_seconds:   过期秒数，默认 1 小时
    """

    def __init__(
        self,
        cache_manager: Optional[CacheManager] = None,
        cache_key: str = AGENT_PROMPT_CACHE_KEY,
        ttl_seconds: float = AGENT_PROMPT_CACHE_TTL_SECONDS,
    ):
        self._cache = cache_manager or MemoryCacheManager()
        self._cache_key = cache_key
        self._ttl_seconds = ttl_seconds

    def get_from_cache(self) -> Optional[Dict[str, str]]:
        """读取缓存快照；未命中 / 读失败 / 内容非映射 → None（回源 DB），不抛错"""
        try:
            value = _AsyncCacheBridge.run(self._cache.get(self._cache_key))
        except Exception:
            logger.warning("读取智能体提示词缓存失败，回源 DB", exc_info=True)
            return None
        if not isinstance(value, dict):
            return None
        return {str(key): value_ for key, value_ in value.items()}

    def save_to_cache(self, prompts: Dict[str, str]) -> None:
        """保存解析结果快照（TTL 1 小时）；失败仅告警"""
        try:
            _AsyncCacheBridge.run(
                self._cache.set(self._cache_key, dict(prompts or {}), self._ttl_seconds)
            )
        except Exception:
            logger.warning("保存智能体提示词到缓存失败", exc_info=True)

    def clear_cache(self) -> None:
        """清除缓存，下次解析强制重载；失败仅告警"""
        try:
            _AsyncCacheBridge.run(self._cache.delete(self._cache_key))
        except Exception:
            logger.warning("清除智能体提示词缓存失败", exc_info=True)


class DatabaseAgentPromptResolver(AgentPromptResolver):
    """
    DB 数据源版智能体提示词解析器（对应 Java AgentPromptResolver 的完整实现）

    叠加回落（load_from_db）：先铺内置智能体（builtin=1）作基线，再让激活智能体
    （active=1）的非空槽位覆盖；后写入者覆盖前者、空白不参与覆盖，以此实现回落。
    两者为同一条时重复覆盖无副作用。

    Args:
        db_client:      5.0 关系库抽象（t_agent_profile / t_agent_prompt）
        cache_manager:  解析结果缓存，默认 RedisAgentPromptCacheManager()（未注入 Redis 时
                        进程内兜底）；测试可注入进程内 AgentPromptCacheManager
    """

    def __init__(
        self,
        db_client: DatabaseClient,
        cache_manager: Optional[AgentPromptCacheManager] = None,
    ):
        self._db = db_client
        self._cache_manager = cache_manager or RedisAgentPromptCacheManager()

    def resolve(self, slot: Optional[AgentPromptSlot]) -> str:
        if slot is None:
            return ""
        value = self.resolve_all().get(slot.name)
        return "" if value is None else value

    def render(self, slot: AgentPromptSlot, slots: Optional[Dict[str, str]]) -> str:
        return PromptTemplateUtils.cleanup_prompt(
            PromptTemplateUtils.fill_slots(self.resolve(slot), slots)
        )

    def resolve_all(self) -> Dict[str, str]:
        """全部槽位的最终生效内容，缺失的槽位不出现在 map 中；缓存命中直接返回"""
        cached = self._cache_manager.get_from_cache()
        if cached is not None:
            return cached
        resolved = self._load_from_db()
        self._cache_manager.save_to_cache(resolved)
        return resolved

    def load_own_prompts(self, agent_id: Optional[str]) -> Dict[str, str]:
        """
        读取某个智能体自身配置的槽位，不做回落，供控制台编辑态展示（对应 Java loadOwnPrompts）

        与叠加回落不同：自身槽位原样返回（content 为 null 时补空串），空白条目也保留。
        """
        own: Dict[str, str] = {}
        if not agent_id or not str(agent_id).strip():
            return own
        rows = self._db.select_rows(
            AGENT_PROMPT_TABLE,
            columns=["slot_key", "content"],
            where=[
                Condition.eq("agent_id", agent_id),
                Condition.eq("deleted", 0),
            ],
        )
        for row in rows:
            content = row.get("content")
            own[row.get("slot_key")] = "" if content is None else content
        return own

    # ==================== 叠加回落（对应 Java loadFromDb / firstByFlag / putNonBlank） ====================

    def _load_from_db(self) -> Dict[str, str]:
        builtin_id = self._first_agent_id_by_flag("builtin")
        if builtin_id is None:
            logger.warning("未找到内置智能体，空槽位将无提示词可回落")
        resolved: Dict[str, str] = {}
        self._put_non_blank(resolved, builtin_id)
        self._put_non_blank(resolved, self._first_agent_id_by_flag("active"))
        return resolved

    def _first_agent_id_by_flag(self, flag_column: str) -> Optional[str]:
        """按标记（builtin/active）取第一条智能体 ID：createTime、id 升序取最小者（对应 Java firstByFlag）"""
        rows = self._db.select_rows(
            AGENT_PROFILE_TABLE,
            columns=["id"],
            where=[
                Condition.eq(flag_column, 1),
                Condition.eq("deleted", 0),
            ],
            order_by=[("create_time", "asc"), ("id", "asc")],
            limit=1,
        )
        if not rows:
            return None
        return rows[0].get("id")

    def _put_non_blank(self, target: Dict[str, str], agent_id: Optional[str]) -> None:
        """后写入者覆盖前者，空白内容不参与覆盖，以此实现回落（对应 Java putNonBlank）"""
        if agent_id is None:
            return
        for slot_key, content in self.load_own_prompts(agent_id).items():
            if content is not None and content.strip():
                target[slot_key] = content
