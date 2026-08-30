// v1.1 P2 Agent REST API（对齐 agent/controller.py：conversations CRUD + meta + stop；
// 统一经 @/shared/api/client Axios interceptor 解包 Result）
import { get, post, put, del } from "@/shared/api/client";

import type { AgentBlock, AgentEngineMeta, AgentPersistedMessageStatus } from "./types";

export interface AgentConversationVO {
  conversationId: string;
  title: string;
  lastTime?: string;
  turns?: number;
}

export interface AgentMessageVO {
  id: string;
  role: string;
  content: string;
  thinkingContent?: string | null;
  // 旧数据为 null 由前端按持久化字段合成
  blocks?: AgentBlock[] | null;
  messageStatus?: AgentPersistedMessageStatus | null;
  createTime?: string;
}

/** GET /agent/v1/conversations：会话列表（lastTime 倒序 + turns 轮数） */
export function listAgentSessions(): Promise<AgentConversationVO[]> {
  return get("/agent/v1/conversations");
}

/** GET /agent/v1/conversations/{id}/messages：消息历史（ASC，含 blocks 轨迹） */
export function listAgentMessages(conversationId: string): Promise<AgentMessageVO[]> {
  return get(`/agent/v1/conversations/${conversationId}/messages`);
}

/** PUT /agent/v1/conversations/{id}/title：重命名（空标题/不存在 → 业务码错误） */
export function renameAgentSession(conversationId: string, title: string): Promise<void> {
  return put(`/agent/v1/conversations/${conversationId}/title`, { title });
}

/** DELETE /agent/v1/conversations/{id}：软删 + 释放运行态 */
export function deleteAgentSession(conversationId: string): Promise<void> {
  return del(`/agent/v1/conversations/${conversationId}`);
}

/** POST /agent/v1/conversations/batch-delete：批量软删 */
export function batchDeleteAgentSessions(conversationIds: string[]): Promise<void> {
  return post("/agent/v1/conversations/batch-delete", { ids: conversationIds });
}

/** GET /agent/v1/meta：引擎探活（offline → ApiError，调用方点亮离线徽标） */
export function getAgentMeta(): Promise<AgentEngineMeta> {
  return get("/agent/v1/meta");
}

/** POST /agent/v1/stop：停止流式任务（query 参数 snake_case task_id，对齐当前后端） */
export function stopAgentTask(taskId: string): Promise<void> {
  return post("/agent/v1/stop", undefined, { params: { task_id: taskId } });
}
