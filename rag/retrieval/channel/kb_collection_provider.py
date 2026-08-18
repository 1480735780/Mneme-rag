"""
有效知识库 collection 提供者（对应 ragent KbCollectionProvider）

全局检索（向量 / 关键词）的唯一「全库范围」来源：只返回未删除（deleted=0）知识库的 collection。
两路全局检索共用此处，保证「全局」语义一致——都以知识库表为准，
而非各自用通配（如 ES 的 kb_*），后者会命中已删除库残留、测试库、旧 schema 等无效索引。

MVP：定义抽象接口 + 内存静态实现；真实 DB 查询（t_knowledge_base）属 C 层 storage/database，
届时注入实现替换即可，RetrievalScopeResolver 面向本抽象编程。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.retrieval.channel.KbCollectionProvider
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from storage.database import Condition, DatabaseClient


class KbCollectionProvider(ABC):
    """有效知识库 collection 提供者接口（对应 Java KbCollectionProvider）"""

    @abstractmethod
    def list_active_collections(self) -> List[str]:
        """
        返回所有有效知识库的 collection 名称（去重、去空）

        Returns:
            List[str]: 有效知识库 collection 列表；无有效库返回空列表
        """
        ...


class StaticKbCollectionProvider(KbCollectionProvider):
    """
    内存静态实现：注入固定列表即作为全库范围（测试 / 无 DB 环境 MVP 兜底）

    Args:
        collections: 有效知识库 collection 列表（去空、去重保序后生效）
    """

    def __init__(self, collections: List[str]):
        seen: List[str] = []
        for name in collections or []:
            if name is None:
                continue
            trimmed = name.strip()
            if trimmed and trimmed not in seen:
                seen.append(trimmed)
        self._collections = seen

    def list_active_collections(self) -> List[str]:
        return list(self._collections)


class DatabaseKbCollectionProvider(KbCollectionProvider):
    """
    关系库实现：查 t_knowledge_base（deleted=0）取 collection_name，过滤空白 + 去重保序

    面向 DatabaseClient 抽象编程，注入 InMemoryDatabaseClient（测试 / MVP）或
    SqlDatabaseClient（真实 SQL）均无感知（对齐 Java 的 BaseMapper<KnowledgeBaseDO> 查询）。

    Args:
        db: 关系库访问抽象（DatabaseClient）
    """

    def __init__(self, db: DatabaseClient):
        self._db = db

    def list_active_collections(self) -> List[str]:
        rows = self._db.select_rows(
            "t_knowledge_base",
            columns=["collection_name"],
            where=[Condition.eq("deleted", 0)],
        )
        result: List[str] = []
        seen = set()
        for row in rows:
            name = (row.get("collection_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result
