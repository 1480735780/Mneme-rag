// M4B T7 智能体档案 REST API（对齐 rag/controller/agent_profile_controller.py）
// - 路由前缀 /agents：GET ""；POST ""；PUT/DELETE "/{id}"；POST "/{id}/activate"；prompts 槽位读写
import { del, get, post, put } from "@/shared/api/client";

import type { AgentListResponse, AgentProfilePayload, AgentPromptsView } from "./types";

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

/** PUT /agents/{id}/prompts/{slotKey}：保存槽位提示词（空白恢复回落） */
export function saveAgentPrompt(id: string, slotKey: string, content: string): Promise<void> {
  return put(`/agents/${encodeURIComponent(id)}/prompts/${encodeURIComponent(slotKey)}`, { content });
}

/** GET /agents/prompt-slots/{slotKey}/default：内置默认提示词 */
export function getDefaultAgentPrompt(slotKey: string): Promise<string> {
  return get(`/agents/prompt-slots/${encodeURIComponent(slotKey)}/default`);
}
