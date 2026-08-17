"""
知识图谱可视化查询服务（对应 ragent GraphQueryService）

后台可视化的查询入口：经 LightRagClient 取图，映射为前端视图 GraphViewVO。
图谱通道未启用（rag.graph.type=none）时无 LightRagClient 实例注入，直接以异常提示，路由本身始终存在。

只依赖 LightRAG 归一化后的图谱语义，不直连 Neo4j：无需在后端引入图数据库驱动与连接配置，换存储亦不受影响。

MVP：依赖 LightRagClient 抽象；未注入时按「通道未启用」抛错（对齐 Java ObjectProvider.getIfAvailable() 为 null）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.graph.GraphQueryService
"""
from __future__ import annotations

import logging
from typing import List, Optional

from rag.graph.client import LightRagClient
from rag.graph.vo import GraphEdge, GraphNode, GraphViewVO

logger = logging.getLogger(__name__)

# 服务端节点数上限（对应 Java mapGraph 的 limit 上界）
_MAX_NODES = 1000


class GraphQueryService:
    """
    知识图谱可视化查询服务（对应 Java GraphQueryService）

    Args:
        light_rag_client: LightRagClient 抽象实现；None 表示图谱通道未启用
    """

    def __init__(self, light_rag_client: Optional[LightRagClient] = None):
        self._client = light_rag_client

    async def get_graph(
        self,
        entity: str,
        collection: str,
        doc: str,
        depth: int,
        limit: int,
    ) -> GraphViewVO:
        """
        查询图谱子图（对应 Java getGraph）

        Args:
            entity:     起点实体名，空则取全图
            collection: 知识库 collectionName，限定只看该库子图，空则不限
            doc:        文档 id，限定只看该文档子图，优先级高于 collection，空则不限
            depth:      子图深度，非正取默认 2
            limit:      节点上限，非正取默认 200，上限 1000

        Returns:
            GraphViewVO: 映射后的前端视图
        """
        client = self._require_client()
        max_depth = depth if depth > 0 else 2
        max_nodes = min(limit, _MAX_NODES) if limit > 0 else 200
        label = entity if entity and entity.strip() else "*"

        # 范围过滤 token：文档最细粒度优先（docId 雪花唯一），否则按知识库 {collectionName}_ 前缀
        # 与 LightRagClient.deleteByCollection/deleteByDoc 同款约定，命中节点 properties.file_path 承载的来源
        token: Optional[str] = None
        if doc and doc.strip():
            token = doc
        elif collection and collection.strip():
            token = collection + "_"

        # 有范围过滤时向 LightRAG 拉宽到服务端上限，保证按 file_path 过滤后仍有足量节点
        fetch_nodes = _MAX_NODES if token is not None else max_nodes
        root = await client.fetch_graph(label, max_depth, fetch_nodes)
        return self._map_graph(root, token, max_nodes)

    async def search_entities(self, keyword: str, limit: int) -> List[str]:
        """检索实体标签，供可视化搜索框；keyword 为空取热门标签（对应 Java searchEntities）"""
        return await self._require_client().fetch_labels(keyword, limit)

    def _require_client(self) -> LightRagClient:
        if self._client is None:
            raise RuntimeError("知识图谱通道未启用（rag.graph.type=none）")
        return self._client

    def _map_graph(
        self, root: Optional[dict], token: Optional[str], limit: int
    ) -> GraphViewVO:
        """
        将 LightRAG 原始 {nodes,edges,is_truncated} 映射为前端视图（对应 Java mapGraph）

        节点展示名取 properties.entity_id、回退 labels[0]、再回退内部 id；类型 / 描述取 properties 对应字段。
        边标签取 properties.keywords、回退 type，关系描述取 properties.description；缺失 id 的边用 source-target 兜底。

        token 非空时按节点 properties.file_path 过滤（只保留来源含 token 的节点），并丢弃两端不全保留的悬空边；
        过滤后仍超 limit 则截断到 limit 并置 truncated，file_path 仅用于内部过滤、不进视图。
        """
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        truncated = False

        if root is not None:
            truncated = bool(root.get("is_truncated", False))
            kept_ids: set = set()

            node_array = root.get("nodes")
            if isinstance(node_array, list):
                for node in node_array:
                    if not isinstance(node, dict):
                        continue
                    node_id = node.get("id") or ""
                    if not node_id.strip():
                        continue
                    props = _props(node)
                    # 范围过滤：token 非空且该节点来源 file_path 不含 token 则剔除
                    if token is not None and token not in (props.get("file_path") or ""):
                        continue
                    # 过滤后按展示上限截断：达上限即标记截断、停止收节点
                    if len(nodes) >= limit:
                        truncated = True
                        break
                    name = props.get("entity_id") or ""
                    if not name.strip():
                        labels = node.get("labels")
                        if isinstance(labels, list) and labels:
                            name = labels[0] or ""
                    kept_ids.add(node_id)
                    nodes.append(
                        GraphNode(
                            id=node_id,
                            name=name if name.strip() else node_id,
                            type=props.get("entity_type") or "",
                            description=_clean_merged(props.get("description") or "", "\n"),
                        )
                    )

            edge_array = root.get("edges")
            if isinstance(edge_array, list):
                for edge in edge_array:
                    if not isinstance(edge, dict):
                        continue
                    source = edge.get("source") or ""
                    target = edge.get("target") or ""
                    if not source.strip() or not target.strip():
                        continue
                    # 两端都在保留集才保留该边，剔除因过滤 / 截断产生的悬空边
                    if source not in kept_ids or target not in kept_ids:
                        continue
                    props = _props(edge)
                    # 关键词同为多来源合并，按 <SEP> 切开去重后用斜杠内联展示
                    label = _clean_merged(props.get("keywords") or "", " / ")
                    if not label:
                        label = edge.get("type") or ""
                    edges.append(
                        GraphEdge(
                            id=edge.get("id") or f"{source}-{target}",
                            source=source,
                            target=target,
                            label=label,
                            description=_clean_merged(props.get("description") or "", "\n"),
                        )
                    )
        return GraphViewVO(nodes=nodes, edges=edges, truncated=truncated)


def _props(node: dict) -> dict:
    """防御式取 properties：非 dict 视为空"""
    properties = node.get("properties")
    return properties if isinstance(properties, dict) else {}


def _clean_merged(raw: str, joiner: str) -> str:
    """
    归一 LightRAG 的多来源合并串（对应 Java cleanMerged）

    <SEP> 是 LightRAG 合并同一实体 / 关系跨来源描述、关键词时的内部分隔符，
    按其切开、逐段去空白与去重后用 joiner 重组。
    """
    if not raw or not raw.strip():
        return ""
    seen: List[str] = []
    for part in raw.split("<SEP>"):
        piece = part.strip()
        if piece and piece not in seen:
            seen.append(piece)
    return joiner.join(seen)