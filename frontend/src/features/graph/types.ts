// M4C T9 知识图谱域类型（对齐 rag/graph/vo.py GraphViewVO/GraphNode/GraphEdge + graph_controller）

/** 图谱节点（GraphNode：camelCase） */
export interface GraphNode {
  id: string;
  name: string;
  type?: string;
  description?: string;
}

/** 图谱边（GraphEdge：camelCase） */
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  description?: string;
}

/** 图谱视图（GraphViewVO：camelCase） */
export interface GraphView {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}
