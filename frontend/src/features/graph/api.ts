// M4C T9 知识图谱 REST API（对齐 rag/controller/graph_controller.py）
// - GET /admin/kg/labels（实体标签）；GET /admin/kg/graph（子图）
import { get } from "@/shared/api/client";

import type { GraphView } from "./types";

/** GET /admin/kg/labels：实体标签（keyword 空取热门；limit 默认 50） */
export function getGraphLabels(keyword?: string, limit = 50): Promise<string[]> {
  return get("/admin/kg/labels", { params: { keyword: keyword || undefined, limit } });
}

/** GET /admin/kg/graph：图谱子图（entity 空取全图；doc 优先级高于 collection） */
export function getGraph(params: { entity?: string; collection?: string; doc?: string; depth?: number; limit?: number } = {}): Promise<GraphView> {
  return get("/admin/kg/graph", {
    params: {
      entity: params.entity || undefined,
      collection: params.collection || undefined,
      doc: params.doc || undefined,
      depth: params.depth ?? 2,
      limit: params.limit ?? 200,
    },
  });
}
