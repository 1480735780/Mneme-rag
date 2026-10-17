// M4B T7 智能体档案 REST API（对齐 rag/controller/agent_profile_controller.py）
// - 路由前缀 /agents：GET ""；POST ""；PUT/DELETE "/{id}"；POST "/{id}/activate"；prompts 槽位读写
import { del, get, post, put } from "@/shared/api/client";

import type {
  AgentListResponse,
  AgentProfilePayload,
  AgentPromptSavedVersion,
  AgentPromptsView,
  PromptVersionList,
  PromptVersionPreview,
} from "./types";

/** GET /agents：档案列表（含编排模式与槽位覆盖率） */
export function getAgents(): Promise<AgentListResponse> {
  return get("/agents");
}

/** POST /agents：创建档案，返回新 id */
export function createAgent(payload: AgentProfilePayload): Promise<string> {
  return post("/agents", payload);
}

/** PUT /agents/{id}：更新档案（PUT 全量：name 必传） */
export function updateAgent(id: string, payload: AgentProfilePayload): Promise<void> {
  return put(`/agents/${encodeURIComponent(id)}`, payload);
}

/** DELETE /agents/{id}：删除档案（激活中拒绝） */
export function deleteAgent(id: string): Promise<void> {
  return del(`/agents/${encodeURIComponent(id)}`);
}

/** POST /agents/{id}/activate：激活（全局仅一条 active） */
export function activateAgent(id: string): Promise<void> {
  return post(`/agents/${encodeURIComponent(id)}/activate`);
}

/** GET /agents/{id}/prompts：槽位配置视图 */
export function getAgentPrompts(id: string): Promise<AgentPromptsView> {
  return get(`/agents/${encodeURIComponent(id)}/prompts`);
}

/** PUT /agents/{id}/prompts/{slotKey}：保存槽位提示词 → 新建草稿版本行（P10.2 T2.4/D8：保存≠生效） */
export function saveAgentPrompt(
  id: string,
  slotKey: string,
  content: string,
): Promise<AgentPromptSavedVersion> {
  return put(`/agents/${encodeURIComponent(id)}/prompts/${encodeURIComponent(slotKey)}`, { content });
}

/** GET /agents/{id}/prompts/{slotKey}/versions：版本历史（含 is-active 徽标与生效来源） */
export function listPromptVersions(id: string, slotKey: string): Promise<PromptVersionList> {
  return get(
    `/agents/${encodeURIComponent(id)}/prompts/${encodeURIComponent(slotKey)}/versions`,
  );
}

/** GET …/versions/{versionId}/preview：diff 预览（当前生效 vs 目标版本，激活/回滚前必看） */
export function previewPromptVersion(
  id: string,
  slotKey: string,
  versionId: string,
): Promise<PromptVersionPreview> {
  return get(
    `/agents/${encodeURIComponent(id)}/prompts/${encodeURIComponent(slotKey)}/versions/${encodeURIComponent(versionId)}/preview`,
  );
}

/** POST …/versions/{versionId}/activate：激活（D8 二次确认，confirm=true 才切指针） */
export function activateAgentPromptVersion(
  id: string,
  slotKey: string,
  versionId: string,
  confirm: boolean,
): Promise<AgentPromptSavedVersion> {
  return post(`/agents/${encodeURIComponent(id)}/prompts/${encodeURIComponent(slotKey)}/versions/${encodeURIComponent(versionId)}/activate`, { confirm });
}

/** POST …/versions/{versionId}/rollback：回滚 = 切指针同路径（D8 无特例，同样二次确认） */
export function rollbackAgentPromptVersion(
  id: string,
  slotKey: string,
  versionId: string,
  confirm: boolean,
): Promise<AgentPromptSavedVersion> {
  return post(`/agents/${encodeURIComponent(id)}/prompts/${encodeURIComponent(slotKey)}/versions/${encodeURIComponent(versionId)}/rollback`, { confirm });
}

/** GET /agents/prompt-slots/{slotKey}/default：内置默认提示词 */
export function getDefaultAgentPrompt(slotKey: string): Promise<string> {
  return get(`/agents/prompt-slots/${encodeURIComponent(slotKey)}/default`);
}
