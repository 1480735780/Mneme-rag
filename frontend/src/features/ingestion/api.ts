// M4C T8 摄取流水线/任务 REST API（对齐 ingestion/controller/pipeline.py + task.py）
// - 流水线：/ingestion/pipelines（分页 pageNo/pageSize/keyword）
// - 任务：/ingestion/tasks（分页 pageNo/pageSize/status；upload multipart）
import { api, del, get, post, put } from "@/shared/api/client";

import type { IngestionPage, IngestionTask, Pipeline, PipelinePayload, TaskNode, TaskResultPayload } from "./types";

/** GET /ingestion/pipelines：分页（pageNo/pageSize/keyword） */
export function getPipelinesPage(
  pageNo = 1,
  pageSize = 10,
  keyword?: string,
): Promise<IngestionPage<Pipeline>> {
  return get("/ingestion/pipelines", { params: { pageNo, pageSize, keyword: keyword || undefined } });
}

/** POST /ingestion/pipelines：创建，返回完整 VO */
export function createPipeline(payload: PipelinePayload): Promise<Pipeline> {
  return post("/ingestion/pipelines", payload);
}

/** PUT /ingestion/pipelines/{id}：更新，返回完整 VO */
export function updatePipeline(id: string, payload: PipelinePayload): Promise<Pipeline> {
  return put(`/ingestion/pipelines/${encodeURIComponent(id)}`, payload);
}

/** GET /ingestion/pipelines/{id}：详情 */
export function getPipeline(id: string): Promise<Pipeline> {
  return get(`/ingestion/pipelines/${encodeURIComponent(id)}`);
}

/** DELETE /ingestion/pipelines/{id}：删除（软删 + 节点物理删） */
export function deletePipeline(id: string): Promise<void> {
  return del(`/ingestion/pipelines/${encodeURIComponent(id)}`);
}

/** GET /ingestion/tasks：任务分页（pageNo/pageSize/status） */
export function getTasksPage(
  pageNo = 1,
  pageSize = 10,
  status?: string,
): Promise<IngestionPage<IngestionTask>> {
  return get("/ingestion/tasks", { params: { pageNo, pageSize, status: status || undefined } });
}

/** GET /ingestion/tasks/{id}：任务详情 */
export function getTask(id: string): Promise<IngestionTask> {
  return get(`/ingestion/tasks/${encodeURIComponent(id)}`);
}

/** GET /ingestion/tasks/{id}/nodes：任务节点运行记录 */
export function getTaskNodes(id: string): Promise<TaskNode[]> {
  return get(`/ingestion/tasks/${encodeURIComponent(id)}/nodes`);
}

/** POST /ingestion/tasks/upload：上传文件并触发任务（multipart：pipelineId + file） */
export async function uploadTaskFile(pipelineId: string, file: File): Promise<TaskResultPayload> {
  const formData = new FormData();
  formData.append("pipelineId", pipelineId);
  formData.append("file", file);
  const resp = await api.post("/ingestion/tasks/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  // 拦截器已解包 ApiResult envelope → resp.data 即业务数据
  return resp.data as TaskResultPayload;
}
