"""
查询改写（对应 ragent RewriteResult + QueryRewriteService + MultiQuestionRewriteService + QueryTermMapping*）

职责划分（本文件按步骤推进，当前已完成步骤 1-4）：
    - RewriteResult：改写结果数据模型（改写问题 + 子问题列表），全链路共享的输入载体。
      下游消费方：
        - IntentResolver（intent 层）按 subQuestions 拆子问题逐个做意图分类；
        - RAGPromptService（prompt 层）用 rewrittenQuestion 作为问题、subQuestions 渲染多问题段；
        - engine 编排（StreamChatPipeline 对应物）透传整个对象。
      纯数据载体，不含业务逻辑，供各消费方共享。
    - QueryRewriteService：改写服务接口（ABC）。rewrite() 单问题改写抽象；
      rewrite_with_split() 改写 + 多问句拆分（history 可选，参与指代消解）。
    - MultiQuestionRewriteService：完整链路实现（对齐 Java MultiQuestionRewriteService）：
        开关关闭 → 术语归一化 + 规则拆分兜底；
        开关开启 → 术语归一化 + LLM 改写/拆分（带最近 2 轮历史），失败回落归一化问题。
    - QueryTermMappingService：术语归一化接口（ABC）。
    - QueryTermMappingUtil：安全归一化替换工具（对齐 Java QueryTermMappingUtil.applyMapping）。
    - TermMappingRule：映射规则数据载体（对应 Java QueryTermMappingDO 的消费子集）。
    - QueryTermMappingCacheManager：进程内缓存（Java 为 Redis 7 天，MVP 退化进程内 dict）。
    - MemoryQueryTermMappingService：内存版术语归一化实现（步骤 4）。规则注入 dict 即生效，
      走「缓存 → 加载排序 → 落缓存」三段流程；仅生效（enabled）且精确匹配（matchType=1）的规则参与替换。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.rewrite.RewriteResult
    - com.nageoffer.ai.ragent.rag.core.rewrite.QueryRewriteService
    - com.nageoffer.ai.ragent.rag.core.rewrite.MultiQuestionRewriteService
    - com.nageoffer.ai.ragent.rag.core.rewrite.QueryTermMappingService
    - com.nageoffer.ai.ragent.rag.core.rewrite.QueryTermMappingUtil
    - com.nageoffer.ai.ragent.rag.core.rewrite.QueryTermMappingCacheManager
    - com.nageoffer.ai.ragent.rag.dao.entity.QueryTermMappingDO
    - com.nageoffer.ai.ragent.rag.constant.RAGConstant.QUERY_REWRITE_AND_SPLIT_PROMPT_PATH
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.llm.chat import LLMService
from core.llm.enums import Tier
from core.llm.schema import ChatRequest, Message, Role
from rag.prompt.formatter import PromptTemplateLoader
from storage.cache import CacheManager, MemoryCacheManager
from storage.cache.bridge import AsyncCacheBridge as _AsyncCacheBridge
from storage.database import Condition, DatabaseClient

logger = logging.getLogger(__name__)

# 改写 + 拆分模板路径（对应 Java RAGConstant.QUERY_REWRITE_AND_SPLIT_PROMPT_PATH）
QUERY_REWRITE_AND_SPLIT_PROMPT_PATH = "prompt/user-question-rewrite.st"

# 表名（对齐 Java DO @TableName）
QUERY_TERM_MAPPING_TABLE = "t_query_term_mapping"

# 缓存 key 与 TTL（对齐 Java QueryTermMappingCacheManager 常量：7 天过期）
QUERY_TERM_MAPPING_CACHE_KEY = "ragent:query-term:mappings"
QUERY_TERM_MAPPING_CACHE_TTL_SECONDS = 7 * 24 * 3600.0

# 模型偶发包裹 Markdown 代码围栏时的剥离（对应 Java LLMResponseCleaner.stripMarkdownCodeFence，
# 后者整体延后上线，此处只做改写链路需要的最小清理）
_CODE_FENCE = re.compile(r"```(?:json)?\s*|\s*```", re.IGNORECASE)


@dataclass(frozen=True)
class RewriteResult:
    """
    查询改写结果（对应 Java RewriteResult record）

    Java record 的两组件 rewrittenQuestion / subQuestions 映射为两个字段；
    frozen dataclass 复刻 record 的不可变 + 值相等语义。

    Attributes:
        rewritten_question: 改写后的检索查询。改写失败/关闭时回落归一化或原始问题，恒非空。
        sub_questions:      子问题列表（复杂问题拆分产物）；空列表表示未拆分，
                            下游（IntentResolver）应回落用 rewritten_question 作为唯一子问题。
    """

    rewritten_question: str
    sub_questions: List[str] = field(default_factory=list)


class QueryRewriteService(ABC):
    """
    用户查询改写服务接口（对应 Java QueryRewriteService）

    将自然语言问题改写为适合向量 / 关键字检索的查询语句；可选的改写 + 多问句拆分。
    """

    @abstractmethod
    async def rewrite(self, user_question: str) -> str:
        """
        将用户问题改写为适合向量 / 关键字检索的简洁查询。

        Args:
            user_question: 原始用户问题

        Returns:
            str: 改写后的检索查询；改写失败 / 关闭时回落归一化/原始问题
        """
        ...

    @abstractmethod
    async def rewrite_with_split(
        self,
        user_question: str,
        history: Optional[List[Message]] = None,
    ) -> RewriteResult:
        """
        可选：改写 + 拆分多问句（对应 Java 两个 rewriteWithSplit 重载，以可选参数折叠）

        返回改写后的查询与其子问题列表；未拆分时子问题列表仅含改写后的查询。
        history 参与指代消解（仅最近 user/assistant 轮次进入 LLM 上下文）。
        """
        ...


class QueryTermMappingService(ABC):
    """
    术语归一化服务接口（对应 Java QueryTermMappingService）

    把用户问题中的业务术语 / 简称归一为检索友好的标准术语。完整映射规则
    （DB 配置 + 缓存 + applyMapping 替换）属步骤 4，本文件先以接口 + 空实现接入完整链路。
    """

    @abstractmethod
    def normalize(self, text: Optional[str]) -> Optional[str]:
        """
        对用户问题做术语归一化。

        Returns:
            Optional[str]: 归一化后的文本；无映射规则或空输入时原样返回
        """
        ...


class NoopQueryTermMappingService(QueryTermMappingService):
    """空实现：原样返回（测试 / 未配置规则时使用）"""

    def normalize(self, text: Optional[str]) -> Optional[str]:
        return text


@dataclass(frozen=True)
class TermMappingRule:
    """
    术语映射规则（对应 Java QueryTermMappingDO 的消费子集）

    Attributes:
        source_term: 用户原始短语
        target_term: 归一化后的目标短语
        match_type:  匹配类型，1=精确匹配（当前仅实现精确匹配，其他类型跳过）
        priority:    优先级，数值越大越先应用（一般长词在前）
        enabled:     是否生效，1=生效 0=禁用
    """

    source_term: str
    target_term: str
    match_type: Optional[int] = 1
    priority: Optional[int] = None
    enabled: int = 1


class QueryTermMappingUtil:
    """术语归一化替换工具（对应 Java QueryTermMappingUtil，全静态方法）"""

    @staticmethod
    def apply_mapping(
        text: Optional[str],
        source_term: Optional[str],
        target_term: Optional[str],
    ) -> Optional[str]:
        """
        安全归一化替换（对应 Java applyMapping）

        从前往后扫描 source_term：
            - 命中但当前位置已经是 target_term 开头（例如文本已是归一化结果），不重复替换，按原文跳过 target；
            - 否则替换为 target_term 并跳过 source_term。
        """
        if text is None or text == "" or source_term is None or source_term == "":
            return text
        if target_term is None:
            target_term = ""

        parts: List[str] = []
        idx = 0
        n = len(text)
        source_len = len(source_term)
        target_len = len(target_term)

        while idx < n:
            hit = text.find(source_term, idx)
            if hit < 0:
                parts.append(text[idx:])
                break
            parts.append(text[idx:hit])
            already_target = (
                target_len > 0
                and hit + target_len <= n
                and text.startswith(target_term, hit)
            )
            if already_target:
                parts.append(text[hit : hit + target_len])
                idx = hit + target_len
            else:
                parts.append(target_term)
                idx = hit + source_len
        return "".join(parts)


class QueryTermMappingCacheManager:
    """
    术语映射缓存管理器（对应 Java QueryTermMappingCacheManager）

    Java 侧缓存于 Redis（key ragent:query-term:mappings，TTL 7 天）；
    Python MVP 无 Redis 基础设施，退化为进程内 dict：命中直接返回、未命中返回 None。
    任何写操作后调用 clear_cache() 使缓存失效。
    """

    def __init__(self):
        self._store: Optional[List[TermMappingRule]] = None

    def get_mappings_from_cache(self) -> Optional[List[TermMappingRule]]:
        """返回映射规则列表；缓存不存在返回 None（返回副本，防外部修改污染缓存）"""
        return list(self._store) if self._store is not None else None

    def save_mappings_to_cache(self, mappings: List[TermMappingRule]) -> None:
        """保存规则列表快照（已排序）"""
        self._store = list(mappings or [])

    def clear_cache(self) -> None:
        """清除缓存，下次 normalize 强制重载"""
        self._store = None


class RedisQueryTermMappingCacheManager(QueryTermMappingCacheManager):
    """
    Redis 版术语映射缓存管理器（对应 Java QueryTermMappingCacheManager）

    缓存的是「术语映射规则列表」的 JSON 快照（key ragent:query-term:mappings，TTL 7 天）。
    经 5.0 CacheManager 抽象存取（生产注入 RedisCacheManager；未注入时进程内
    MemoryCacheManager 兜底），JSON 序列化与 Redis 异常兜底由 CacheManager 收口；
    本层再兜一层桥接 / 反序列化异常，语义对齐 Java：
      读失败 / JSON 非法 → None（回源 DB）；写 / 删失败仅告警不抛错。
    任何映射规则增删改后必须 clear_cache()，否则改动直到过期才生效。

    Args:
        cache_manager: 5.0 缓存抽象实例（生产注入 RedisCacheManager）
        cache_key:     缓存键
        ttl_seconds:   过期秒数，默认 7 天
    """

    def __init__(
        self,
        cache_manager: Optional[CacheManager] = None,
        cache_key: str = QUERY_TERM_MAPPING_CACHE_KEY,
        ttl_seconds: float = QUERY_TERM_MAPPING_CACHE_TTL_SECONDS,
    ):
        self._cache = cache_manager or MemoryCacheManager()
        self._cache_key = cache_key
        self._ttl_seconds = ttl_seconds

    def get_mappings_from_cache(self) -> Optional[List[TermMappingRule]]:
        """读取缓存快照；未命中 / 读失败 / 反序列化异常 → None（回源 DB），不抛错"""
        try:
            value = _AsyncCacheBridge.run(self._cache.get(self._cache_key))
        except Exception:
            logger.warning("读取术语映射缓存失败，回源 DB", exc_info=True)
            return None
        if not isinstance(value, list):
            return None
        try:
            return [_rule_from_dict(entry) for entry in value]
        except Exception:
            logger.warning("术语映射缓存反序列化失败，回源 DB", exc_info=True)
            return None

    def save_mappings_to_cache(self, mappings: List[TermMappingRule]) -> None:
        """保存规则列表快照（已排序，TTL 7 天）；失败仅告警"""
        try:
            _AsyncCacheBridge.run(
                self._cache.set(
                    self._cache_key,
                    [_rule_to_dict(rule) for rule in (mappings or [])],
                    self._ttl_seconds,
                )
            )
        except Exception:
            logger.warning("保存术语映射到缓存失败", exc_info=True)

    def clear_cache(self) -> None:
        """清除缓存，下次 normalize 强制回源；失败仅告警"""
        try:
            _AsyncCacheBridge.run(self._cache.delete(self._cache_key))
        except Exception:
            logger.warning("清除术语映射缓存失败", exc_info=True)


def load_term_mappings_from_db(db: DatabaseClient) -> List[TermMappingRule]:
    """
    从关系库加载术语映射规则（对应 Java QueryTermMappingService.loadMappings 的 DB 部分）

    查 t_query_term_mapping（enabled=1），行转 TermMappingRule 后按 Java 排序规则排序。
    面向 DatabaseClient 抽象编程，注入 InMemoryDatabaseClient（测试 / MVP）或
    SqlDatabaseClient（真实 SQL）均无感知。
    """
    rows = db.select_rows(
        QUERY_TERM_MAPPING_TABLE,
        where=[Condition.eq("enabled", 1)],
    )
    return _sort_mappings([_rule_from_row(row) for row in rows])


def _rule_from_row(row: Dict[str, Any]) -> TermMappingRule:
    """t_query_term_mapping 行 → TermMappingRule（对应 Java BeanUtil.toBean 的消费子集）"""
    return TermMappingRule(
        source_term=row.get("source_term") or "",
        target_term=row.get("target_term") or "",
        match_type=row.get("match_type"),
        priority=row.get("priority"),
        enabled=row.get("enabled"),
    )


def _sort_mappings(rules: List[TermMappingRule]) -> List[TermMappingRule]:
    """
    应用顺序排序（对齐 Java loadMappings 的 Comparator 链）

    Java：comparing(priority, nullsLast()).reversed() → priority 降序、null 排最前；
    再 thenComparing(sourceTerm 长度, reverseOrder()) → 同优先级长词在前。
    """
    return sorted(
        rules,
        key=lambda r: (
            1 if r.priority is None else 0,  # null 排最前（对齐 Java reversed(nullsLast)）
            r.priority if r.priority is not None else 0,
            len(r.source_term) if r.source_term else 0,
        ),
        reverse=True,
    )


def _apply_mappings(text: str, mappings: List[TermMappingRule]) -> str:
    """按顺序应用生效的精确匹配规则（对齐 Java QueryTermMappingService.normalize 主循环）"""
    result = text
    for mapping in mappings:
        if mapping.enabled is None or mapping.enabled == 0:
            continue
        if mapping.match_type is not None and mapping.match_type != 1:
            continue
        if not mapping.source_term or not mapping.target_term:
            continue
        result = QueryTermMappingUtil.apply_mapping(
            result, mapping.source_term, mapping.target_term
        )
    return result


def _rule_to_dict(rule: TermMappingRule) -> Dict[str, Any]:
    """规则 → JSON 可序列化 dict（供 Redis 缓存往返）"""
    return {
        "source_term": rule.source_term,
        "target_term": rule.target_term,
        "match_type": rule.match_type,
        "priority": rule.priority,
        "enabled": rule.enabled,
    }


def _rule_from_dict(data: Dict[str, Any]) -> TermMappingRule:
    """dict → 规则（缓存反序列化）"""
    return TermMappingRule(
        source_term=data.get("source_term") or "",
        target_term=data.get("target_term") or "",
        match_type=data.get("match_type"),
        priority=data.get("priority"),
        enabled=data.get("enabled"),
    )


class MemoryQueryTermMappingService(QueryTermMappingService):
    """
    内存版术语归一化实现（对应 Java QueryTermMappingService，步骤 4）

    以注入的规则列表为唯一数据源，走「缓存 → 加载排序 → 落缓存」三段流程，
    语义与 Java 的 loadMappings + normalize 一致：
        - 仅 enabled=1 且 match_type=1（精确匹配）的规则参与替换；
        - 应用顺序：priority 降序（null 最后）→ source_term 长度降序（长词在前）；
        - 每个规则按 QueryTermMappingUtil.apply_mapping 安全替换。
    真实后端（DB 加载规则）实现 QueryTermMappingService 后注入替换即可。

    Args:
        rules: 映射规则列表
        cache_manager: 结果缓存，默认新建进程内缓存
    """

    def __init__(
        self,
        rules: Optional[List[TermMappingRule]] = None,
        cache_manager: Optional[QueryTermMappingCacheManager] = None,
    ):
        self._rules = list(rules or [])
        self._cache_manager = cache_manager or QueryTermMappingCacheManager()

    def normalize(self, text: Optional[str]) -> Optional[str]:
        if text is None or text == "":
            return text
        mappings = self._load_mappings()
        if not mappings:
            return text
        return _apply_mappings(text, mappings)

    def _load_mappings(self) -> List[TermMappingRule]:
        """加载生效规则：缓存优先，未命中从注入源加载、排序后落缓存（对应 Java loadMappings）"""
        cached = self._cache_manager.get_mappings_from_cache()
        if cached:
            return cached
        enabled_rules = _sort_mappings([r for r in self._rules if r.enabled])
        self._cache_manager.save_mappings_to_cache(enabled_rules)
        return enabled_rules


class DatabaseQueryTermMappingService(QueryTermMappingService):
    """
    关系库版术语归一化实现（对应 Java QueryTermMappingService）

    以 t_query_term_mapping（enabled=1）为唯一数据源，走「缓存 → DB 加载排序 → 落缓存」
    三段流程；仅生效（enabled）且精确匹配（matchType=1）的规则参与替换（同 Memory 版）。

    Args:
        db_client:      5.0 关系库抽象（t_query_term_mapping）
        cache_manager:  映射规则缓存，默认 RedisQueryTermMappingCacheManager()（未注入
                        Redis 时进程内兜底）；测试可注入进程内 QueryTermMappingCacheManager
    """

    def __init__(
        self,
        db_client: DatabaseClient,
        cache_manager: Optional[QueryTermMappingCacheManager] = None,
    ):
        self._db = db_client
        self._cache_manager = cache_manager or RedisQueryTermMappingCacheManager()

    def normalize(self, text: Optional[str]) -> Optional[str]:
        if text is None or text == "":
            return text
        mappings = self._load_mappings()
        if not mappings:
            return text
        return _apply_mappings(text, mappings)

    def _load_mappings(self) -> List[TermMappingRule]:
        """加载生效规则：缓存优先，未命中从 DB 加载、排序后落缓存（对应 Java loadMappings）"""
        cached = self._cache_manager.get_mappings_from_cache()
        if cached:
            return cached
        mappings = load_term_mappings_from_db(self._db)
        self._cache_manager.save_mappings_to_cache(mappings)
        return mappings


class MultiQuestionRewriteService(QueryRewriteService):
    """
    查询预处理完整链路：改写 + 拆分多问句（对应 Java MultiQuestionRewriteService）

    行为对齐 Java：
        - 开关关闭（enabled=False）：术语归一化 + 规则拆分兜底，不走 LLM；
        - 开关开启：先术语归一化，再走 LLM 改写/拆分；
          LLM 调用失败 / 解析失败一律回落「归一化问题 + 单子问题」，不抛错；
        - LLM 请求只带最近 4 条 user/assistant 历史（2 轮对话），过滤 System 摘要避免 token 浪费。

    Args:
        llm_service:           LLM 服务（async chat）
        template_loader:       模板加载器，默认 PromptTemplateLoader()
        enabled:               改写开关（对应 Java RAGConfigProperties.queryRewriteEnabled）
        term_mapping_service:  术语归一化服务，默认 NoopQueryTermMappingService()（步骤 4 注入真实实现）
    """

    def __init__(
        self,
        llm_service: LLMService,
        template_loader: Optional[PromptTemplateLoader] = None,
        enabled: bool = True,
        term_mapping_service: Optional[QueryTermMappingService] = None,
    ):
        self._llm = llm_service
        self._template_loader = template_loader or PromptTemplateLoader()
        self._enabled = enabled
        self._term_mapping = term_mapping_service or NoopQueryTermMappingService()

    async def rewrite(self, user_question: str) -> str:
        if not user_question or not user_question.strip():
            return user_question or ""
        result = await self.rewrite_with_split(user_question, None)
        return result.rewritten_question

    async def rewrite_with_split(
        self,
        user_question: str,
        history: Optional[List[Message]] = None,
    ) -> RewriteResult:
        if not user_question or not user_question.strip():
            return RewriteResult(user_question or "", [user_question or ""])
        if not self._enabled:
            normalized = self._term_mapping.normalize(user_question)
            subs = self._rule_based_split(normalized)
            return RewriteResult(normalized, subs)
        normalized_question = self._term_mapping.normalize(user_question)
        return await self._call_llm_rewrite_and_split(normalized_question, user_question, history)

    # ==================== LLM 改写 + 拆分 ====================

    async def _call_llm_rewrite_and_split(
        self,
        normalized_question: str,
        original_question: str,
        history: Optional[List[Message]],
    ) -> RewriteResult:
        """加载模板 → 构造请求 → FAST 档调用 → 解析；失败回落归一化问题（对应 Java callLLMRewriteAndSplit）"""
        system_prompt = self._template_loader.load(QUERY_REWRITE_AND_SPLIT_PROMPT_PATH)
        request = self._build_rewrite_request(system_prompt, normalized_question, history)
        fallback = RewriteResult(normalized_question, [normalized_question])
        try:
            raw = await self._llm.chat(request, tier=Tier.FAST)
            parsed = self._parse_rewrite_and_split(raw)
            return parsed if parsed is not None else fallback
        except Exception:
            logger.warning("查询改写 LLM 调用失败，使用归一化问题兜底：original='%s'", original_question, exc_info=True)
            return fallback

    def _build_rewrite_request(
        self,
        system_prompt: str,
        question: str,
        history: Optional[List[Message]],
    ) -> ChatRequest:
        """构造改写请求：system + 最近 2 轮 user/assistant 历史 + user（对应 Java buildRewriteRequest）"""
        messages: List[Message] = []
        if system_prompt and system_prompt.strip():
            messages.append(Message.system(system_prompt))
        if history:
            recent = [m for m in history if m.role in (Role.USER, Role.ASSISTANT)]
            messages.extend(recent[-4:])  # 最多保留最近 4 条消息（2 轮对话）
        messages.append(Message.user(question))
        return ChatRequest(messages=messages, temperature=0.1, topP=0.3, thinking=False)

    @staticmethod
    def _parse_rewrite_and_split(raw: Optional[str]) -> Optional[RewriteResult]:
        """
        解析 LLM 返回的改写 JSON {rewrite, sub_questions}（对应 Java parseRewriteAndSplit）

        Returns:
            Optional[RewriteResult]: 解析成功返回结果；raw 空白 / JSON 非法 /
                rewrite 缺失或空白返回 None（触发兜底）。sub_questions 为空时回落 [rewrite]。
        """
        if raw is None or not raw.strip():
            return None
        cleaned = _CODE_FENCE.sub("", raw).strip()
        try:
            obj = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        rewrite = obj.get("rewrite")
        if not isinstance(rewrite, str) or not rewrite.strip():
            return None
        rewrite = rewrite.strip()
        subs: List[str] = []
        raw_subs = obj.get("sub_questions")
        if isinstance(raw_subs, list):
            for el in raw_subs:
                if isinstance(el, str) and el.strip():
                    subs.append(el.strip())
        if not subs:
            subs = [rewrite]
        return RewriteResult(rewrite, subs)

    # ==================== 规则拆分兜底 ====================

    @staticmethod
    def _rule_based_split(question: Optional[str]) -> List[str]:
        """
        兜底：按常见分隔符拆分多问句（对应 Java ruleBasedSplit）

        按 [?？。；;\\n]+ 拆分、trim、去空；全部为空则回落整个问题；
        每个子句若非 ?/？ 结尾补 "？"。
        """
        if not question:
            return [question or ""]
        parts = [p.strip() for p in re.split(r"[?？。；;\n]+", question)]
        parts = [p for p in parts if p]
        if not parts:
            return [question]
        return [s if s.endswith("？") or s.endswith("?") else s + "？" for s in parts]
