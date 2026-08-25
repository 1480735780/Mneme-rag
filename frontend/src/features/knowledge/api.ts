// M2 知识域 REST API（对齐 knowledge/controller/kb.py + document.py + chunk.py）
// 统一经 Axios interceptor 解包；上传/分块等端点返回 VO（camelCase）
import { api, del, get, patch, post, put } from "@/shared/api/client";

import type {
  IngestionSpecSchema,
  KnowledgeBase,
  KnowledgeBaseCreatePayload,
  KnowledgeBaseUpdatePayload,
  KnowledgeChunk,
  KnowledgeChunkPageParams,
  KnowledgeDocument,
  KnowledgeDocumentChunkLog,
  KnowledgeDocumentPageParams,
  KnowledgeDocumentSearchItem,
  KnowledgeDocumentUpdatePayload,
  KnowledgeDocumentUploadPayload,
  PageResult,
} from "./types";

// ---- 知识库（K1-K5） ----

/** GET /knowledge-base：分页列表（name like + 每库 documentCount） */
export function getKnowledgeBasesPage(
  current = 1,
  size = 10,
  name?: string,
): Promise<PageResult<KnowledgeBase>> {
  return get("/knowledge-base", { params: { current, size, name: name || undefined } });
}

/** POST /knowledge-base：创建（返回新 id） */
export function createKnowledgeBase(payload: KnowledgeBaseCreatePayload): Promise<string> {
  return post("/knowledge-base", payload);
}

/** PUT /knowledge-base/{id}：重命名/更新 */
export function updateKnowledgeBase(id: string, payload: KnowledgeBaseUpdatePayload): Promise<void> {
  return put(`/knowledge-base/${id}`, payload);
}

/** DELETE /knowledge-base/{id}：删除（有未删文档拒绝） */
export function deleteKnowledgeBase(id: string): Promise<void> {
  return del(`/knowledge-base/${id}`);
}

/** GET /knowledge-base/{id}：详情（文档页标题展示库名） */
export function getKnowledgeBase(id: string): Promise<KnowledgeBase> {
  return get(`/knowledge-base/${id}`);
}

// ---- 文档（D1-D12） ----

/** D1：摄取配置表单 schema（动态渲染 ingestionSpec） */
export function getIngestionSpecSchema(): Promise<IngestionSpecSchema> {
  return get("/knowledge-base/docs/ingestion-spec-schema");
}

/** D2：上传文档（multipart：file + 表单字段，字段名对齐后端 camelCase） */
export function uploadDocument(
  kbId: string,
  payload: KnowledgeDocumentUploadPayload,
): Promise<KnowledgeDocument> {
  const formData = new FormData();
  formData.append("sourceType", payload.sourceType);
  if (payload.file) {
    formData.append("file", payload.file);
  }
  if (payload.sourceLocation) {
    formData.append("sourceLocation", payload.sourceLocation);
  }
  if (payload.scheduleEnabled !== undefined) {
    formData.append("scheduleEnabled", String(payload.scheduleEnabled));
  }
  if (payload.scheduleCron) {
    formData.append("scheduleCron", payload.scheduleCron);
  }
  if (payload.processMode) {
    formData.append("processMode", payload.processMode);
  }
  if (payload.ingestionSpec) {
    formData.append("ingestionSpec", payload.ingestionSpec);
  }
  if (payload.pipelineId) {
    formData.append("pipelineId", payload.pipelineId);
  }
  return post(`/knowledge-base/${kbId}/docs/upload`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

/** D3：开始分块（CAS 防重） */
export function startDocumentChunk(docId: string): Promise<void> {
  return post(`/knowledge-base/docs/${docId}/chunk`);
}

/** D5：文档详情 */
export function getDocument(docId: string): Promise<KnowledgeDocument> {
  return get(`/knowledge-base/docs/${docId}`);
}

/** D6：更新文档 */
export function updateDocument(docId: string, payload: KnowledgeDocumentUpdatePayload): Promise<void> {
  return put(`/knowledge-base/docs/${docId}`, payload);
}

/** D7：文档分页（keyword/status 过滤） */
export function getDocumentsPage(
  kbId: string,
  params: KnowledgeDocumentPageParams = {},
): Promise<PageResult<KnowledgeDocument>> {
  return get(`/knowledge-base/${kbId}/docs`, {
    params: {
      current: params.current ?? 1,
      size: params.size ?? 10,
      status: params.status || undefined,
      keyword: params.keyword || undefined,
    },
  });
}

/** D8：全局文档搜索 */
export function searchKnowledgeDocuments(keyword: string, limit = 8): Promise<KnowledgeDocumentSearchItem[]> {
  return get("/knowledge-base/docs/search", { params: { keyword, limit } });
}

/** D9：启用/禁用文档 */
export function enableDocument(docId: string, enabled: boolean): Promise<void> {
  return patch(`/knowledge-base/docs/${docId}/enable`, null, { params: { value: enabled } });
}

/** D4：删除文档（RUNNING 拒删） */
export function deleteDocument(docId: string): Promise<void> {
  return del(`/knowledge-base/docs/${docId}`);
}

/** D10：分块日志分页 */
export function getChunkLogsPage(docId: string, current = 1, size = 10): Promise<PageResult<KnowledgeDocumentChunkLog>> {
  return get(`/knowledge-base/docs/${docId}/chunk-logs`, { params: { current, size } });
}

/** D11：markdown 预览（String data） */
export function previewDocument(docId: string): Promise<string> {
  return get(`/knowledge-base/docs/${docId}/preview`);
}

/** D12：源文件流（blob，非 Result envelope），供前端触发浏览器下载 */
export async function downloadDocumentFile(docId: string): Promise<Blob> {
  const resp = await api.get(`/knowledge-base/docs/${docId}/file`, { responseType: "blob" });
  return resp.data as Blob;
}

// ---- 文档块（C1-C6） ----

/** C1：分块分页（enabled 可选过滤） */
export function getChunksPage(docId: string, params: KnowledgeChunkPageParams = {}): Promise<PageResult<KnowledgeChunk>> {
  return get(`/knowledge-base/docs/${docId}/chunks`, {
    params: {
      current: params.current ?? 1,
      size: params.size ?? 10,
      enabled: params.enabled ?? undefined,
    },
  });
}

/** C2：新增手工 Chunk */
export function createChunk(
  docId: string,
  payload: { content?: string; index?: number | null; chunkId?: string },
): Promise<KnowledgeChunk> {
  return post(`/knowledge-base/docs/${docId}/chunks`, payload);
}

/** C3：更新 Chunk 内容 */
export function updateChunk(docId: string, chunkId: string, payload: { content?: string }): Promise<void> {
  return put(`/knowledge-base/docs/${docId}/chunks/${chunkId}`, payload);
}

/** C4：删除 Chunk */
export function deleteChunk(docId: string, chunkId: string): Promise<void> {
  return del(`/knowledge-base/docs/${docId}/chunks/${chunkId}`);
}

/** C5：启用/禁用单条 Chunk */
export function toggleChunk(docId: string, chunkId: string, enabled: boolean): Promise<void> {
  return patch(`/knowledge-base/docs/${docId}/chunks/${chunkId}/enable`, null, { params: { value: enabled } });
}

/** C6：批量启用/禁用（≤500；body 可缺省时仅 value 生效） */
export function batchToggleChunks(docId: string, enabled: boolean, chunkIds: string[]): Promise<void> {
  return patch(`/knowledge-base/docs/${docId}/chunks/batch-enable`, { chunkIds }, { params: { value: enabled } });
}
