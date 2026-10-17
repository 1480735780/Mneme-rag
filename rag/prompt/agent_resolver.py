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
      agent_prompt（agentId + slotKey + content）+ agent_prompt_version（不可变版本行，P10.2）
      三张表，叠加回落规则：先铺内置智能体（builtin=1）作基线，再让激活智能体（active=1）
      的非空槽位覆盖，空白内容不参与覆盖，以此实现回落；多实例按 createTime、id 升序取第一条。

P10.2 T2.3 缓存时效（D9）：
    - 版本号入缓存 key：缓存键 = 基键:指针指纹。指纹由「内置/激活两档案 + 两 agent 全部槽位
      的 active_version_id」构成，带 AGENT_PROMPT_POINTER_TTL_SECONDS（5s）迷你缓存直读——
      激活/回滚切指针即指纹必变 → key 必然 miss → 机制自然失效，不依赖手动 clear_cache 兜底
      （旧 key 条目靠自身 TTL 过期，短生命周期换简单性）；
    - 读侧指针感知（D8）：槽位行 active_version_id 非空 → 生效内容取版本行 content；
      指针 NULL → 存量 content 列（未版本化兼容，行为与旧版一致）。

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

import hashlib
import logging
import time
from typing import Callable, Dict, Optional

from rag.dao.agent_dao import AgentPromptVersionDao
from rag.prompt.builder import (
    DEFAULT_AGENT_PROMPTS,
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

# 指针指纹直读 TTL（P10.2 T2.3，D9）：版本号入缓存 key，激活即 key 必然 miss、
# 机制自然失效，不依赖手动 clear_cache；指纹这里只读 5s——激活到生效 ≤5s 的承诺兑现点
AGENT_PROMPT_POINTER_TTL_SECONDS = 5.0


class RedisAgentPromptCacheManager(AgentPromptCacheManager):
    """
    Redis 版智能体提示词缓存管理器（对应 Java AgentPromptCacheManager）

    缓存的是激活智能体叠加自定义提示词之后的结果，命中即可直接取用。
    P10.2 T2.3（D9）：解析结果按「指针指纹」分键（key = 基键:指纹），激活/回滚切指针
    后新指纹 key 必然 miss、机制自然失效——**不再依赖手动 clear_cache 兜底**；
    旧 key 条目靠自身 TTL 过期。clear_cache 保留为运维逃生舱（不挂调用点）。

    Args:
        cache_manager: 5.0 缓存抽象实例（生产注入 RedisCacheManager；默认进程内 MemoryCacheManager）
        cache_key:     基键
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

    def get_from_cache(self, fingerprint: Optional[str] = None) -> Optional[Dict[str, str]]:
        """读取缓存快照；未命中 / 读失败 / 内容非映射 → None（回源 DB），不抛错"""
        try:
            value = _AsyncCacheBridge.run(self._cache.get(self._key(fingerprint)))
        except Exception:
            logger.warning("读取智能体提示词缓存失败，回源 DB", exc_info=True)
            return None
        if not isinstance(value, dict):
            return None
        return {str(key): value_ for key, value_ in value.items()}

    def save_to_cache(self, prompts: Dict[str, str], fingerprint: Optional[str] = None) -> None:
        """保存解析结果快照（TTL 1 小时）；失败仅告警"""
        try:
            _AsyncCacheBridge.run(
                self._cache.set(self._key(fingerprint), dict(prompts or {}), self._ttl_seconds)
            )
        except Exception:
            logger.warning("保存智能体提示词到缓存失败", exc_info=True)

    def clear_cache(self, fingerprint: Optional[str] = None) -> None:
        """清除缓存（运维逃生舱；D9 版本号化后正常链路不再依赖手动失效）；失败仅告警"""
        try:
            _AsyncCacheBridge.run(self._cache.delete(self._key(fingerprint)))
        except Exception:
            logger.warning("清除智能体提示词缓存失败", exc_info=True)

    def _key(self, fingerprint: Optional[str]) -> str:
        """解析结果缓存键：无指纹 = 基键（降级路径）；有指纹 = 基键:指纹（D9 版本号化）"""
        return self._cache_key if not fingerprint else f"{self._cache_key}:{fingerprint}"


class DatabaseAgentPromptResolver(AgentPromptResolver):
    """
    DB 数据源版智能体提示词解析器（对应 Java AgentPromptResolver 的完整实现）

    叠加回落（load_from_db）：先铺内置智能体（builtin=1）作基线，再让激活智能体
    （active=1）的非空槽位覆盖；后写入者覆盖前者、空白不参与覆盖，以此实现回落。
    两者为同一条时重复覆盖无副作用。

    Args:
        db_client:      5.0 关系库抽象（t_agent_profile / t_agent_prompt / t_agent_prompt_version）
        cache_manager:  解析结果缓存，默认 RedisAgentPromptCacheManager()（未注入 Redis 时
                        进程内兜底）；测试可注入进程内 AgentPromptCacheManager
        clock:          指纹迷你缓存的时钟源（默认 time.monotonic；测试可注入虚拟时钟）
    """

    def __init__(
        self,
        db_client: DatabaseClient,
        cache_manager: Optional[AgentPromptCacheManager] = None,
        clock: Optional[Callable[[], float]] = None,
        pointer_ttl_seconds: float = AGENT_PROMPT_POINTER_TTL_SECONDS,
    ):
        self._db = db_client
        self._cache_manager = cache_manager or RedisAgentPromptCacheManager()
        # P10.2 T2.3（D9）：指纹直读的迷你缓存（默认 5s TTL，时钟可注入供测试实测 ≤5s 生效）
        self._clock: Callable[[], float] = clock or time.monotonic
        self._pointer_ttl_seconds = max(0.0, float(pointer_ttl_seconds))
        self._pointer_fingerprint: Optional[str] = None
        self._pointer_fingerprint_until: float = 0.0

    @property
    def _prompt_version_dao(self) -> AgentPromptVersionDao:
        """版本数据访问（按需惰性建：只依赖同一 db 客户端，无额外装配面）"""
        if not hasattr(self, "_prompt_version_dao_ref"):
            self._prompt_version_dao_ref = AgentPromptVersionDao(self._db)
        return self._prompt_version_dao_ref

    def resolve(self, slot: Optional[AgentPromptSlot]) -> str:
        if slot is None:
            return ""
        value = self.resolve_all().get(slot.name)
        if value:
            return value
        # 未配置回落内置默认（仅对已声明默认值的槽位生效，其余仍空串）
        return DEFAULT_AGENT_PROMPTS.get(slot.name, "")

    def render(self, slot: AgentPromptSlot, slots: Optional[Dict[str, str]]) -> str:
        return PromptTemplateUtils.cleanup_prompt(
            PromptTemplateUtils.fill_slots(self.resolve(slot), slots)
        )

    def resolve_all(self) -> Dict[str, str]:
        """全部槽位的最终生效内容，缺失的槽位不出现在 map 中；缓存命中直接返回

        D9：缓存 key 带「active_version_id 指针指纹」（5s 直读 TTL）——激活/回滚切
        指针后指纹必变，key 必然 miss，机制自然失效；旧 key 条目靠自身 TTL 过期。
        指纹读取失败时回退基键（缓存命中可能滞后一个指纹 TTL，与 Java 版改动_until
        过期/手动的语义同级，属可观测降级而非错误）。
        """
        fingerprint = self._peek_pointer_fingerprint()
        if fingerprint:
            cached = self._cache_manager.get_from_cache(fingerprint)
            if cached is not None:
                return cached
            resolved = self._load_from_db()
            self._cache_manager.save_to_cache(resolved, fingerprint)
            return resolved
        # 指纹不可用（DB 查询异常）→ 基键兜底走旧路径：命中即返回，保推理可用
        cached = self._cache_manager.get_from_cache(None)
        if cached is not None:
            return cached
        resolved = self._load_from_db()
        self._cache_manager.save_to_cache(resolved, None)
        return resolved

    # ==================== 指针指纹（P10.2 T2.3，D9：版本号入缓存 key） ====================

    def _peek_pointer_fingerprint(self) -> Optional[str]:
        """内置/激活档案 + 两 agent 槽位指针集的指纹；5s 迷你缓存直读（时钟可注入）"""
        now = self._clock()
        if self._pointer_fingerprint is not None and now < self._pointer_fingerprint_until:
            return self._pointer_fingerprint
        try:
            fingerprint = self._compute_pointer_fingerprint()
        except Exception:  # noqa: BLE001 — 指纹属可观测旁路，失败不影响推理主路径
            logger.warning("提示词指针指纹计算失败，本窗口内沿用基键缓存", exc_info=True)
            return None
        self._pointer_fingerprint = fingerprint
        self._pointer_fingerprint_until = now + self._pointer_ttl_seconds
        return fingerprint

    def _compute_pointer_fingerprint(self) -> str:
        builtin_id = self._first_agent_id_by_flag("builtin")
        active_id = self._first_agent_id_by_flag("active")
        parts = [builtin_id or "-", active_id or "-"]
        for agent_id in (builtin_id, active_id):
            if not agent_id:
                continue
            rows = self._db.select_rows(
                AGENT_PROMPT_TABLE,
                columns=["slot_key", "active_version_id"],
                where=[
                    Condition.eq("agent_id", agent_id),
                    Condition.eq("deleted", 0),
                ],
            )
            for row in sorted(rows, key=lambda each: str(each.get("slot_key") or "")):
                pointer = row.get("active_version_id")
                parts.append(f"{row.get('slot_key')}={pointer or 'none'}")
        digest = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]  # noqa: S324
        return digest

    def load_own_prompts(self, agent_id: Optional[str]) -> Dict[str, str]:
        """
        读取某个智能体自身配置的槽位，不做回落，供控制台编辑态展示（对应 Java loadOwnPrompts）

        P10.2 T2.3（D8 读侧）：槽位行 active_version_id 非空 → 生效内容取版本行 content；
        指针 NULL → 存量 content 列（未版本化兼容）。自身槽位原样返回（content 为 null
        时补空串），空白条目也保留。
        """
        own: Dict[str, str] = {}
        if not agent_id or not str(agent_id).strip():
            return own
        rows = self._db.select_rows(
            AGENT_PROMPT_TABLE,
            columns=["slot_key", "content", "active_version_id"],
            where=[
                Condition.eq("agent_id", agent_id),
                Condition.eq("deleted", 0),
            ],
        )
        for row in rows:
            content = self._effective_content_for_row(agent_id, row)
            own[row.get("slot_key")] = "" if content is None else content
        return own

    def _effective_content_for_row(
        self, agent_id: Optional[str], row: Dict[str, object]
    ) -> Optional[str]:
        """D8 读侧约定：指针版本内容 > 存量 content 列（指针失效/版本丢失时回落，保推理不断）"""
        pointer = row.get("active_version_id")
        if pointer:
            version = self._prompt_version_dao.get_by_id(str(pointer))
            if version is not None:
                return version.get("content")
            logger.warning(
                "提示词指针指向的版本缺失，回落存量 content（agent_id=%s slot_key=%s pointer=%s）",
                agent_id,
                row.get("slot_key"),
                pointer,
            )
        content = row.get("content")
        return None if content is None else str(content)

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
