# -*- coding: utf-8 -*-
"""
rag.service.intent_tree_admin_service - 意图树管理 service（对应 Java IntentTreeServiceImpl 管理写路径）

域职责（M5 5.4）：
    - trees 列表：由 dao.list_all 组装管理端树（parent_code 分组 + 递归 children，对齐 getFullTree）；
    - create / update / delete / batch(enable|disable|delete)；**写后清 IntentTreeCacheManager 缓存**
      ——使引擎读路径（load_intent_tree_from_db）强制回源（对齐 Java clearIntentTreeCache）；
    - 业务校验（对齐 Java）：
      · intentCode 重复 →「意图标识已存在: code」；
      · kind 缺省 KB(0)；TOPIC(2) 级 + KB 必须至少指定一个目标知识库 →
        「TOPIC级别的RAG检索节点必须至少指定一个目标知识库」；
      · 节点级 TopK 仅允许正整数（缺省 None 回退全局）→「节点级 TopK 必须大于 0」；
      · update 前置负载校验；kind != KB 时清空 collection 绑定；
      · batch 前置 ids 非空 + 存在校验；disable 校验子树已启用子节点全包含；delete 校验子树全包含。

边界（§4.4）：collection **知识库存在性**校验依赖 knowledge 域（P5），本层仅做**结构化**校验
（trim/去重 + TOPIC 规则），DB 存在性校验留待 P5；读路径（引擎树组装）复用 load_intent_tree_from_db。

collection_names/examples 统一存 JSON 数组字符串（对齐 tree.py `_parse_string_list` 惯例），读侧解析。

方案 B：本层输出 snake_case dict，camelCase 序列化由 controller 边界 pydantic VO（5.7）完成。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.ingestion.service.impl.IntentTreeServiceImpl
    - com.nageoffer.ai.ragent.rag.enums.IntentKind / IntentLevel
    - com.nageoffer.ai.ragent.rag.controller.vo.IntentNodeTreeVO
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from common.exception.business import ClientException
from rag.dao.intent_node_dao import IntentNodeAdminDao
from rag.intent.tree import IntentTreeCacheManager, RedisIntentTreeCacheManager

logger = logging.getLogger(__name__)

# 意图类型（对齐 Java IntentKind）
INTENT_KIND_KB = 0
INTENT_KIND_SYSTEM = 1
INTENT_KIND_MCP = 2

# 意图层级（对齐 Java IntentLevel）
INTENT_LEVEL_DOMAIN = 0
INTENT_LEVEL_CATEGORY = 1
INTENT_LEVEL_TOPIC = 2


class IntentTreeAdminService:
    """意图树管理服务（对应 Java IntentTreeServiceImpl 管理路径）"""

    def __init__(
        self,
        dao: IntentNodeAdminDao,
        cache_manager: Optional[IntentTreeCacheManager] = None,
    ):
        self._dao = dao
        # 写后清缓存对象（对齐 Java IntentTreeCacheManager；缺省 Redis 版，进程内兜底）
        self._cache_manager = cache_manager or RedisIntentTreeCacheManager()

    # ==================== 树（trees） ====================

    def tree(self) -> List[Dict]:
        """完整管理树（对齐 getFullTree：parent_code 分组 + 递归 children）"""
        rows = self._dao.list_all()
        parent_map: Dict[str, List[Dict]] = {}
        for node in rows:
            parent_map.setdefault(node.get("parent_code") or "ROOT", []).append(node)
        # visited 集合兜底：即便历史存在脏环数据，也避免 _build_tree 无限递归（RecursionError）
        visited: set = set()
        return [
            _build_tree(node, parent_map, visited) for node in parent_map.get("ROOT", [])
        ]

    # ==================== create / update / delete ====================

    def create(
        self,
        *,
        intent_code: str,
        name: str,
        level: Optional[int] = None,
        kind: Optional[int] = None,
        parent_code: Optional[str] = None,
        description: Optional[str] = None,
        collection_names: Optional[List[str]] = None,
        kb_id: Optional[str] = None,
        mcp_tool_id: Optional[str] = None,
        examples: Optional[List[str]] = None,
        top_k: Optional[int] = None,
        sort_order: Optional[int] = None,
        enabled: Optional[int] = None,
        param_prompt_template: Optional[str] = None,
        prompt_snippet: Optional[str] = None,
        prompt_template: Optional[str] = None,
    ) -> str:
        """创建意图节点，返回主键 ID；写后清缓存"""
        if self._dao.exists_by_intent_code(intent_code):
            raise ClientException(f"意图标识已存在: {intent_code}")

        kind = kind if kind is not None else INTENT_KIND_KB
        parent_code = _trim_to_none(parent_code)
        if parent_code is not None:
            self._assert_parent_exists(parent_code)
        collections = _normalize_collections(collection_names, kb_id) if kind == INTENT_KIND_KB else []
        self._assert_topic_kb_has_collection(level, kind, collections)
        top_k = _normalize_top_k(top_k)

        nid = self._dao.create({
            "intent_code": intent_code,
            "name": name,
            "level": level,
            "parent_code": parent_code,
            "description": description,
            "kind": kind,
            "mcp_tool_id": mcp_tool_id,
            "examples": _to_json_array(examples),
            "top_k": top_k,
            "sort_order": sort_order if sort_order is not None else 0,
            "enabled": enabled if enabled is not None else 1,
            "collection_names": _to_json_array(collections),
            "prompt_snippet": prompt_snippet,
            "prompt_template": prompt_template,
            "param_prompt_template": param_prompt_template,
            "kb_id": _primary_kb_id(collections, kb_id) if kind == INTENT_KIND_KB else None,
            "collection_name": _first_or_none(collections),
        })
        self._clear_cache()
        return nid

    def update(
        self,
        nid: str,
        *,
        name: Optional[str] = None,
        level: Optional[int] = None,
        parent_code: Optional[str] = None,
        description: Optional[str] = None,
        collection_names: Optional[List[str]] = None,
        collection_name: Optional[str] = None,
        mcp_tool_id: Optional[str] = None,
        examples: Optional[List[str]] = None,
        top_k: Optional[int] = None,
        kind: Optional[int] = None,
        sort_order: Optional[int] = None,
        enabled: Optional[int] = None,
        param_prompt_template: Optional[str] = None,
        prompt_snippet: Optional[str] = None,
        prompt_template: Optional[str] = None,
    ) -> None:
        """更新意图节点（仅刷传非空字段）；写后清缓存"""
        node = self._load_or_raise(nid)
        values: Dict[str, object] = {}
        if name is not None:
            values["name"] = name
        if level is not None:
            values["level"] = level
        if parent_code is not None:
            new_parent = _trim_to_none(parent_code)
            # 环检测（父改到自身/自身后代）+ 父存在性；根（空白→None）直接放行
            if new_parent != node.get("parent_code"):
                if new_parent is not None:
                    self._assert_parent_valid(node, new_parent)
            values["parent_code"] = new_parent
        if description is not None:
            values["description"] = description
        if examples is not None:
            values["examples"] = _to_json_array(examples)
        if mcp_tool_id is not None:
            values["mcp_tool_id"] = mcp_tool_id
        if top_k is not None:
            values["top_k"] = _normalize_top_k(top_k)
        if sort_order is not None:
            values["sort_order"] = sort_order
        if enabled is not None:
            values["enabled"] = enabled
        if param_prompt_template is not None:
            values["param_prompt_template"] = param_prompt_template
        if prompt_snippet is not None:
            values["prompt_snippet"] = prompt_snippet
        if prompt_template is not None:
            values["prompt_template"] = prompt_template

        # collection 绑定：优先 collection_names，其次 collection_name（对齐 Java 双入口）
        effective_kind = kind if kind is not None else node.get("kind")
        if collection_names is not None:
            effective_collections = _normalize_collections(collection_names, None)
        elif collection_name is not None:
            effective_collections = _normalize_collections([collection_name], None)
        else:
            effective_collections = None

        if kind is not None:
            values["kind"] = kind
        if effective_kind != INTENT_KIND_KB:
            # 非 KB 节点清空 collection 绑定（对齐 Java）
            values["collection_names"] = _to_json_array([])
            values["collection_name"] = None
            values["kb_id"] = None
        elif effective_collections is not None:
            self._assert_topic_kb_has_collection(
                level if level is not None else node.get("level"),
                INTENT_KIND_KB,
                effective_collections,
            )
            values["collection_names"] = _to_json_array(effective_collections)
            values["collection_name"] = _first_or_none(effective_collections)
            values["kb_id"] = _primary_kb_id(effective_collections, node.get("kb_id"))
        else:
            # KB 且未传 collection：按既有集合做 TOPIC 规则最终校验
            existing = _as_list(node.get("collection_names"))
            self._assert_topic_kb_has_collection(
                level if level is not None else node.get("level"),
                INTENT_KIND_KB,
                existing,
            )

        self._dao.update(nid, values)
        self._clear_cache()

    def delete(self, nid: str) -> None:
        """软删意图节点（对齐 deleteNode）；有未删子节点先拒绝防孤儿；写后清缓存"""
        node = self._load_or_raise(nid)
        # 防孤儿：存在未删子节点则拒绝，与 batch_delete「子树全包含」形成对称护栏
        # （若直接软删父节点，子节点挂不进子树在 tree() 静默消失，数据却仍留在 list_all）
        if self._has_children(node.get("intent_code")):
            raise ClientException(f"请先删除子节点：{node.get('name') or node.get('intent_code')}")
        self._dao.soft_delete(nid)
        self._clear_cache()

    # ==================== batch ====================

    def batch_enable(self, ids: List[str]) -> None:
        """批量启用（对齐 batchEnableNodes）"""
        _validate_ids(ids)
        self._dao.batch_enable(_normalized_ids(ids))
        self._clear_cache()

    def batch_disable(self, ids: List[str]) -> None:
        """批量停用（对齐 batchDisableNodes）：校验子树已启用子节点全包含"""
        target_nodes = self._load_targets(ids)
        all_nodes = self._dao.list_all()
        children_map = _build_children_map(all_nodes)
        target_set = {n.get("id") for n in target_nodes}
        for node in target_nodes:
            enabled_not_selected = [
                d for d in _collect_descendants(node.get("intent_code"), children_map)
                if _as_int(d.get("enabled")) == 1 and d.get("id") not in target_set
            ]
            if enabled_not_selected:
                raise ClientException(
                    f"批量停用失败：节点 [{node.get('name')}] 存在已启用的子节点未包含在本次操作中"
                    f"（如：{_summarize(enabled_not_selected)}），请先选择全量子节点"
                )
        self._dao.batch_disable(_normalized_ids(ids))
        self._clear_cache()

    def batch_delete(self, ids: List[str]) -> None:
        """批量软删（对齐 batchDeleteNodes）：校验子树全包含"""
        target_nodes = self._load_targets(ids)
        all_nodes = self._dao.list_all()
        children_map = _build_children_map(all_nodes)
        target_set = {n.get("id") for n in target_nodes}
        for node in target_nodes:
            not_selected = [
                d for d in _collect_descendants(node.get("intent_code"), children_map)
                if d.get("id") not in target_set
            ]
            if not_selected:
                enabled_descendants = [d for d in not_selected if _as_int(d.get("enabled")) == 1]
                if enabled_descendants:
                    raise ClientException(
                        f"批量删除失败：节点 [{node.get('name')}] 存在已启用的子节点未包含在本次操作中"
                        f"（如：{_summarize(enabled_descendants)}），请先选择全量子节点"
                    )
                raise ClientException(
                    f"批量删除失败：节点 [{node.get('name')}] 未包含全量子节点"
                    f"（如：{_summarize(not_selected)}），请先勾选完整子树后再删除"
                )
        self._dao.batch_delete(_normalized_ids(ids))
        self._clear_cache()

    # ==================== 内部辅助 ====================

    def _load_or_raise(self, nid: str) -> Dict:
        """按 id 查节点（软删过滤）；缺失抛「节点不存在或已删除」（对齐 Java getById 校验）"""
        node = self._dao.find_by_id(nid)
        if node is None:
            raise ClientException(f"节点不存在或已删除: id={nid}")
        return node

    # ---------- 父节点存在性 / 环检测 / 子节点（防孤儿） ----------

    def _active_codes(self) -> set:
        """全部未删节点的 intent_code 集合（用于父节点存在性校验）"""
        return {
            n.get("intent_code") for n in self._dao.list_all() if n.get("intent_code")
        }

    def _assert_parent_exists(self, parent_code: str) -> None:
        """父节点 code 必须指向存在的未删节点，否则子节点将成孤儿静默消失"""
        if parent_code not in self._active_codes():
            raise ClientException(f"父节点不存在: {parent_code}")

    def _assert_parent_valid(self, node: Dict, parent_code: str) -> None:
        """父节点校验：存在 + 非自身 + 非自身后代（防环，避免 _build_tree 无限递归）"""
        if parent_code == node.get("intent_code"):
            raise ClientException(f"父节点不能是自身: {parent_code}")
        if parent_code not in self._active_codes():
            raise ClientException(f"父节点不存在: {parent_code}")
        all_nodes = self._dao.list_all()
        children_map = _build_children_map(all_nodes)
        desc_codes = {
            d.get("intent_code")
            for d in _collect_descendants(node.get("intent_code"), children_map)
            if d.get("intent_code")
        }
        if parent_code in desc_codes:
            raise ClientException(f"父节点不能是自身的后代节点: {parent_code}")

    def _has_children(self, intent_code: Optional[str]) -> bool:
        """是否存在未删子节点（parent_code == intent_code），供单点 delete 防孤儿"""
        if not intent_code:
            return False
        return any(
            n.get("parent_code") == intent_code for n in self._dao.list_all()
        )

    def _load_targets(self, ids: List[str]) -> List[Dict]:
        """批量目标节点存在校验（对齐 listAndValidateTargetNodes）"""
        normalized = _normalized_ids(ids)
        if not normalized:
            raise ClientException("节点ID不能为空")
        targets = self._dao.list_all()
        by_id = {n.get("id"): n for n in targets}
        missing = [nid for nid in normalized if nid not in by_id]
        if missing:
            raise ClientException(f"节点不存在或已删除: {missing[:5]}")
        return [by_id[nid] for nid in normalized]

    @staticmethod
    def _assert_topic_kb_has_collection(level, kind, collections) -> None:
        """TOPIC(2) 级 + KB(0) 必须至少指定一个目标知识库"""
        if _as_int(level) == INTENT_LEVEL_TOPIC and _as_int(kind) == INTENT_KIND_KB and not collections:
            raise ClientException("TOPIC级别的RAG检索节点必须至少指定一个目标知识库")

    def _clear_cache(self) -> None:
        """写后清 intent 树缓存：引擎读路径下次强制回源（对齐 Java clearIntentTreeCache）"""
        try:
            self._cache_manager.clear_cache()
        except Exception:  # noqa: BLE001 —— 缓存清理失败仅告警，不阻断写操作
            logger.warning("意图树缓存清理失败", exc_info=True)


# ==================== 树组装 / 工具 ====================


def _build_tree(node: Dict, parent_map: Dict[str, List[Dict]], visited: set) -> Dict:
    """递归构建子树（children 缺省省略，对齐 Java buildTree）；visited 防脏数据成环导致无限递归"""
    vid = node.get("id")
    if vid in visited:
        return _to_node_vo(node)  # 环兜底：已成环节点不再展开 children
    visited.add(vid)
    vo = _to_node_vo(node)
    children = parent_map.get(node.get("intent_code") or "", [])
    if children:
        vo["children"] = [_build_tree(child, parent_map, visited) for child in children]
    return vo


def _to_node_vo(node: Dict) -> Dict:
    """节点行 → 管理树 VO（方案 B：snake_case；enabled→bool，collection_names/examples 反序列化）"""
    return {
        "id": node.get("id"),
        "kb_id": node.get("kb_id"),
        "intent_code": node.get("intent_code"),
        "name": node.get("name"),
        "level": node.get("level"),
        "parent_code": node.get("parent_code"),
        "description": node.get("description"),
        "collection_name": node.get("collection_name"),
        "collection_names": _as_list(node.get("collection_names")),
        "mcp_tool_id": node.get("mcp_tool_id"),
        "top_k": node.get("top_k"),
        "kind": node.get("kind"),
        "sort_order": node.get("sort_order"),
        "prompt_snippet": node.get("prompt_snippet"),
        "prompt_template": node.get("prompt_template"),
        "param_prompt_template": node.get("param_prompt_template"),
        "enabled": _as_int(node.get("enabled")) == 1,
        "examples": _as_list(node.get("examples")),
    }


def _build_children_map(nodes: List[Dict]) -> Dict[str, List[Dict]]:
    """按 parent_code 分组（parent 为空归 ROOT，对齐 Java buildChildrenMap）"""
    result: Dict[str, List[Dict]] = {}
    for node in nodes:
        result.setdefault(node.get("parent_code") or "ROOT", []).append(node)
    return result


def _collect_descendants(intent_code, children_map: Dict[str, List[Dict]]) -> List[Dict]:
    """BFS 收集子节点集合（不含自身，对齐 Java collectDescendants）"""
    result: List[Dict] = []
    stack = list(children_map.get(intent_code or "", []))
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(children_map.get(current.get("intent_code") or "", []))
    return result


def _normalize_collections(collection_names, kb_id: Optional[str]) -> List[str]:
    """归一 collection 列表：去空、trim、去重保序（对齐 normalizeCollectionNames + kbId 兜底）"""
    if kb_id and not collection_names:
        return [kb_id]  # 兼容旧 kbId 输入（简化：以 kbId 为无冗余的 collection 名）
    if not collection_names:
        return []
    seen: List[str] = []
    for value in collection_names:
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _to_json_array(values) -> Optional[str]:
    """列表 → JSON 数组字符串（对齐 Java GSON.toJson）；空/None → '[]'"""
    return json.dumps(list(values or []), ensure_ascii=False)


def _as_list(value) -> List:
    """collection_names/examples 反序列化：兼容 InMemory list / SQL JSON 字符串"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:  # noqa: BLE001
        return []


def _normalize_top_k(top_k: Optional[int]) -> Optional[int]:
    """节点级 TopK 规范化：null 回退全局；仅允许正整数（对齐 Java normalizeTopK）"""
    if top_k is None:
        return None
    if _as_int(top_k) <= 0:
        raise ClientException("节点级 TopK 必须大于 0")
    return _as_int(top_k)


def _primary_kb_id(collections: List[str], fallback_kb_id: Optional[str]) -> Optional[str]:
    """primary kb_id = 首个 collection（无则回退传入 kbId，对齐 primaryKbId）"""
    return collections[0] if collections else fallback_kb_id


def _first_or_none(values) -> Optional[str]:
    return values[0] if values else None


def _normalized_ids(ids: Optional[List[str]]) -> List[str]:
    """归一批量 ids：去 None/空白 + 去重（对齐 Java normalizeIds）"""
    if not ids:
        return []
    result: List[str] = []
    for value in ids:
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _validate_ids(ids: Optional[List[str]]) -> None:
    """批量操作至少选一个节点（对齐 Java Assert.notEmpty「请至少选择一个节点」）"""
    if not ids or not _normalized_ids(ids):
        raise ClientException("请至少选择一个节点")


def _summarize(nodes: List[Dict]) -> str:
    """汇总节点名（限 3 个，名缺失回落 intentCode，对齐 Java summarizeNodeNames）"""
    return "、".join(
        str(node.get("name") or node.get("intent_code")) for node in nodes[:3]
    )


def _as_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trim_to_none(value: Optional[str]) -> Optional[str]:
    """Trim 首尾空格，空白串归一 None（对齐 Java StrUtil.trimToNull）"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None