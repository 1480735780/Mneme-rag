// M4B T5 术语映射 REST API（对齐 rag/controller/query_term_mapping_controller.py）
// - 路由前缀 /mappings：GET/POST ""；GET/PUT/DELETE "/{id}"；分页参数 current/size/keyword
import { del, get, post, put } from "@/shared/api/client";

import type { TermMapping, TermMappingPage, TermMappingPayload } from "./types";

/** GET /mappings：分页查询（priority asc + update_time desc） */
export function getTermMappingsPage(
  current = 1,
  size = 10,
  keyword?: string,
): Promise<TermMappingPage> {
  return get("/mappings", { params: { current, size, keyword: keyword || undefined } });
}

/** GET /mappings/{id}：详情 */
export function getTermMapping(id: string): Promise<TermMapping> {
  return get(`/mappings/${encodeURIComponent(id)}`);
}

/** POST /mappings：创建，返回新 id */
export function createTermMapping(payload: TermMappingPayload): Promise<string> {
  return post("/mappings", payload);
}

/** PUT /mappings/{id}：更新 */
export function updateTermMapping(id: string, payload: TermMappingPayload): Promise<void> {
  return put(`/mappings/${encodeURIComponent(id)}`, payload);
}

/** DELETE /mappings/{id}：删除（物理删） */
export function deleteTermMapping(id: string): Promise<void> {
  return del(`/mappings/${encodeURIComponent(id)}`);
}
