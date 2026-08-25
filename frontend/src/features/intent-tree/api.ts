// M4B T6 意图树 REST API（对齐 rag/controller/intent_tree_controller.py）
// - GET /intent-tree/trees；POST /intent-tree；PUT/DELETE /intent-tree/{id}；POST /intent-tree/batch/{enable,disable,delete}
import { del, get, post, put } from "@/shared/api/client";

import type { IntentNode, IntentNodePayload } from "./types";

/** GET /intent-tree/trees：完整管理树（parent_code 递归 children） */
export function getIntentTree(): Promise<IntentNode[]> {
  return get("/intent-tree/trees");
}

/** POST /intent-tree：创建节点，返回新 id */
export function createIntentNode(payload: IntentNodePayload): Promise<string> {
  return post("/intent-tree", payload);
}

/** PUT /intent-tree/{id}：更新节点 */
export function updateIntentNode(id: string, payload: IntentNodePayload): Promise<void> {
  return put(`/intent-tree/${encodeURIComponent(id)}`, payload);
}

/** DELETE /intent-tree/{id}：删除节点（有未删子节点拒绝） */
export function deleteIntentNode(id: string): Promise<void> {
  return del(`/intent-tree/${encodeURIComponent(id)}`);
}

/** POST /intent-tree/batch/enable：批量启用 */
export function batchEnableIntentNodes(ids: string[]): Promise<void> {
  return post("/intent-tree/batch/enable", { ids });
}

/** POST /intent-tree/batch/disable：批量停用（子树全包含校验） */
export function batchDisableIntentNodes(ids: string[]): Promise<void> {
  return post("/intent-tree/batch/disable", { ids });
}

/** POST /intent-tree/batch/delete：批量软删（子树全包含校验） */
export function batchDeleteIntentNodes(ids: string[]): Promise<void> {
  return post("/intent-tree/batch/delete", { ids });
}
