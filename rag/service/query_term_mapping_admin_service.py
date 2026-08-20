# -*- coding: utf-8 -*-
"""
rag.service.query_term_mapping_admin_service - 术语映射管理 service（对应 Java QueryTermMappingAdminService/Impl）

域职责（M5 5.3）：
    - CRUD + 分页（管理端）；写路径（create/update/delete）**写后清缓存**
      ——clearCache() 使检索词改写读路径的映射快照失效，下次即时回源（对齐 Java 业务语义）；
    - 默认值：match_type=1、priority=0、enabled=1（对齐 Java create 缺省）；
    - Trim/blank 校验：source_term/target_term 必填非空（对齐 Assert.notBlank「原始词/目标词不能为空」）；
    - update 仅刷传非空字段 + 前置负载校验（不存在抛「映射规则不存在」，对齐 loadById）。

边界（§4.4）：本层复用既有 `QueryTermMappingAdminDao`（写路径 + 分页），**不重复实现读取/改写应用**
（读路径仍由 rag/rewrite/query_rewrite.py 的 DatabaseQueryTermMappingService 承载）。

方案 B：本层输出 snake_case dict（source_term/match_type/create_time），camelCase 序列化由
controller 边界 pydantic VO（vo.py，步骤 5.7）完成。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.QueryTermMappingAdminService / Impl
    - com.nageoffer.ai.ragent.rag.controller.vo.QueryTermMappingVO
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from common.exception.business import ClientException
from rag.dao.term_mapping_dao import QueryTermMappingAdminDao
from rag.rewrite.query_rewrite import QueryTermMappingCacheManager, RedisQueryTermMappingCacheManager

logger = logging.getLogger(__name__)

# 缺省值（对齐 Java create：matchType=1 / priority=0 / enabled=1）
DEFAULT_MATCH_TYPE = 1
DEFAULT_PRIORITY = 0
DEFAULT_ENABLED = 1

# 分页缺省（对齐 MyBatis-Plus Page 常用缺省）
DEFAULT_CURRENT = 1
DEFAULT_SIZE = 10


class QueryTermMappingAdminService:
    """术语映射管理服务（对应 Java QueryTermMappingAdminServiceImpl）"""

    def __init__(
        self,
        dao: QueryTermMappingAdminDao,
        cache_manager: Optional[QueryTermMappingCacheManager] = None,
    ):
        self._dao = dao
        # 写后清缓存对象（对齐 Java QueryTermMappingCacheManager）。
        # 注意：生产必须注入与读路径（DatabaseQueryTermMappingService）**同一共享实例**
        #   （见 app/wiring.py query_term_mapping_cache）。缺省 RedisQueryTermMappingCacheManager()
        #   自建私有实例，跨实例清缓存为 no-op（进程内 profile 下读路径永远旧快照）——仅供测试兜底。
        if cache_manager is None:
            logger.warning("query_term_mapping_admin_service 未注入共享缓存实例，写后清缓存可能为空操作")
        self._cache_manager = cache_manager or RedisQueryTermMappingCacheManager()

    def create(
        self,
        *,
        source_term: Optional[str] = None,
        target_term: Optional[str] = None,
        match_type: Optional[int] = None,
        priority: Optional[int] = None,
        enabled: Optional[bool] = None,
        remark: Optional[str] = None,
    ) -> str:
        """创建映射规则，返回主键 ID；source_term/target_term 必填，缺省补默认值；写后清缓存"""
        source_term = _trim_to_none(source_term)
        target_term = _trim_to_none(target_term)
        if not source_term:
            raise ClientException("原始词不能为空")
        if not target_term:
            raise ClientException("目标词不能为空")
        mid = self._dao.create({
            "source_term": source_term,
            "target_term": target_term,
            "match_type": match_type if match_type is not None else DEFAULT_MATCH_TYPE,
            "priority": priority if priority is not None else DEFAULT_PRIORITY,
            "enabled": _enabled_int(enabled) if enabled is not None else DEFAULT_ENABLED,
            "remark": _trim_to_none(remark),
        })
        self._clear_cache()
        return mid

    def update(
        self,
        mid: str,
        *,
        source_term: Optional[str] = None,
        target_term: Optional[str] = None,
        match_type: Optional[int] = None,
        priority: Optional[int] = None,
        enabled: Optional[bool] = None,
        remark: Optional[str] = None,
    ) -> None:
        """更新映射规则（仅刷传非空字段；source_term/target_term 若传需非空；不存在抛「映射规则不存在」）"""
        self._load_or_raise(mid)
        values: Dict[str, object] = {}
        if source_term is not None:
            source_term = _trim_to_none(source_term)
            if not source_term:
                raise ClientException("原始词不能为空")
            values["source_term"] = source_term
        if target_term is not None:
            target_term = _trim_to_none(target_term)
            if not target_term:
                raise ClientException("目标词不能为空")
            values["target_term"] = target_term
        if match_type is not None:
            values["match_type"] = match_type
        if priority is not None:
            values["priority"] = priority
        if enabled is not None:
            values["enabled"] = _enabled_int(enabled)
        if remark is not None:
            values["remark"] = _trim_to_none(remark)
        self._dao.update(mid, values)
        self._clear_cache()

    def delete(self, mid: str) -> None:
        """物理删除映射规则（不存在抛「映射规则不存在」）；写后清缓存"""
        self._load_or_raise(mid)
        self._dao.delete(mid)
        self._clear_cache()

    def query_by_id(self, mid: str) -> Dict:
        """查询映射规则详情（不存在抛「映射规则不存在」）"""
        return _to_vo(self._load_or_raise(mid))

    def page_query(
        self,
        current: Optional[int] = DEFAULT_CURRENT,
        size: Optional[int] = DEFAULT_SIZE,
        keyword: Optional[str] = None,
    ) -> Dict:
        """分页查询（priority asc + update_time desc + 可选 keyword 对 source_term/target_term 模糊）"""
        current = current if current and current >= 1 else DEFAULT_CURRENT
        size_val = DEFAULT_SIZE if size is None else max(0, size)
        offset = (current - 1) * size_val if size_val > 0 else 0
        rows, total = self._dao.page_query(
            limit=size_val,
            offset=offset,
            keyword=_trim_to_none(keyword),
        )
        return {
            "records": [_to_vo(r) for r in rows],
            "total": total,
            "current": current,
            "size": size_val,
        }

    # ==================== 内部辅助 ====================

    def _load_or_raise(self, mid: str) -> Dict:
        """按 id 查映射规则（无软删列）；缺失抛「映射规则不存在」（对齐 Java loadById）"""
        row = self._dao.find_by_id(mid)
        if row is None:
            raise ClientException("映射规则不存在")
        return row

    def _clear_cache(self) -> None:
        """写后清缓存：使改写读路径的映射快照失效，下次强制回源（对齐 Java clearCache）"""
        try:
            self._cache_manager.clear_cache()
        except Exception:  # noqa: BLE001 —— 缓存清理失败仅告警，不阻断写操作（对齐 Redis 兜底语义）
            logger.warning("术语映射缓存清理失败", exc_info=True)


def _to_vo(row: Dict) -> Dict:
    """数据库行 → VO dict（对齐 QueryTermMappingVO；enabled int → bool）"""
    return {
        "id": row.get("id"),
        "source_term": row.get("source_term"),
        "target_term": row.get("target_term"),
        "match_type": row.get("match_type"),
        "priority": row.get("priority"),
        "enabled": bool(row.get("enabled")),
        "remark": row.get("remark"),
        "create_time": row.get("create_time"),
        "update_time": row.get("update_time"),
    }


def _enabled_int(enabled: bool) -> int:
    """enabled bool → 0/1（对齐 Java enabled ? 1 : 0）"""
    return 1 if enabled else 0


def _trim_to_none(value: Optional[str]) -> Optional[str]:
    """Trim 首尾空格，空白串归一 None（对齐 Java StrUtil.trimToNull）"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None