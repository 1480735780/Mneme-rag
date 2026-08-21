# -*- coding: utf-8 -*-
"""
knowledge.support.vector_target_resolver - 向量落点派生（对应 Java VectorTargetResolver）

知识库配置（L2）+ 部署级模型注册表（L1）→ VectorTarget：

    - partition        取自知识库的 collection_name（逻辑分区键，非物理空间名）
    - embedding_model  取自知识库配置，是知识库级约束，不允许回落到系统默认
    - dimension        经部署级模型注册表解析（缺失抛 ClientException）

单独成一个组件是为了让「落点身份怎么算出来」只有一个产生地。原先每个写向量的调用点各自从知识
库取模型、各自决定要不要回落默认，于是上传路径用知识库配置的模型、管道路径用系统默认模型，
同一个分区里混进了两种语义空间的向量。

偏离说明（与 Java 的差异）：
    - 维度来源：Java 从部署级标量 `rag.default.dimension`（Python 对应 VectorProperties.dimension，
      默认 1024）直接取整型计（VectorTargetResolver.java L51-54）；Python 侧该标量带硬编码默认值，
      「未配置 → ClientException」分支会永远不可达、沦为摆设。
    - 本实现按 P5 plan 0.4 决策改为「经模型注册表（AIModelConfig.embedding.candidates）按模型 id
      解析维度」，注册表缺失该模型或未声明维度（None/<=0）→ ClientException。已申报偏离并留档。
    - KB 判空 / embedding_model 必填的顺序与报错语义逐行对齐 Java（L45-50）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.support.VectorTargetResolver
"""
from __future__ import annotations

from typing import Any, Dict

from common.exception.business import ClientException
from storage.vector.schema import VectorTarget


class VectorTargetResolver:
    """向量落点派生：知识库行（dict）→ VectorTarget（无状态，可复用单实例）

    Args:
        model_config: 部署级模型配置（AIModelConfig 或同形对象），其 `.embedding.candidates`
                      为嵌入模型注册表，每项含 `id` 与 `dimension`（可选）。

    异常分层说明：注册表 miss / 未声明维度 → ClientException。依据——KB 行是**可编辑的作者
    配置**，引用一个部署未注册的模型属资深操作者的配置疏漏，把 KB 改成部署里已有的模型即愈；
    从「谁能修复」看是调用方可修复错误（4xx），而非部署本体配置缺陷（5xx）。Java 走标量无此
    路径、无先例可抄，选 4xx 并在此论证留档。

    collection_name 不校验：对齐 Java（Java L55 直接透传 getCollectionName()，无判空）；DB 列
    `collection_name VARCHAR(64) NOT NULL UNIQUE`（schema_pg.sql L171/L177），非空由 DDL 与 N1
    创建时的 collection_name 重名校验在数据库与 service 层保证，resolver 不重复设防。若某条
    KB 行被外部直写清空，VectorTarget.__post_init__ 会以 ValueError 兜底（非本类业务路径）。
    """

    def __init__(self, model_config: Any):
        self._model_config = model_config

    def resolve(self, kb: Dict[str, Any]) -> VectorTarget:
        """派生落点，缺配置直接失败而不是回落默认值"""
        if kb is None:
            raise ClientException("知识库不存在")
        embedding_model = (kb.get("embedding_model") or "").strip()
        if not embedding_model:
            raise ClientException(f"知识库未配置嵌入模型：kbId={kb.get('id')}")
        dimension = self._resolve_dimension(embedding_model)
        return VectorTarget(
            partition=kb.get("collection_name"),
            embedding_model=embedding_model,
            dimension=dimension,
        )

    def _resolve_dimension(self, model_id: str) -> int:
        """按模型 id 在部署级注册表中解析向量维度；缺失或未声明 → ClientException（不回落默认）"""
        for candidate in self._model_config.embedding.candidates:
            if candidate.id == model_id:
                if candidate.dimension is not None and candidate.dimension > 0:
                    return candidate.dimension
                raise ClientException(
                    f"部署配置中嵌入模型 [{model_id}] 未声明向量维度，无法解析落点"
                )
        raise ClientException(
            f"部署配置中找不到嵌入模型 [{model_id}] 的注册信息，无法解析落点"
        )