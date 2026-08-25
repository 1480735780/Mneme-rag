// M1 #1 聊天 REST API（对齐 conversation_controller / message_feedback_controller /
// recommended_question_controller / chat_controller.stop；统一经 Axios interceptor 解包）
import { del, get, post, put } from "@/shared/api/client";

import type { ChatMessage, Conversation, RecommendedQuestionsPayload } from "./types";

/** GET /conversations：当前用户会话列表（lastTime 倒序） */
export function listConversations(): Promise<Conversation[]> {
  return get("/conversations");
}

/** PUT /conversations/{id}：重命名 */
export function renameConversation(conversationId: string, title: string): Promise<void> {
  return put(`/conversations/${conversationId}`, { title });
}

/** DELETE /conversations/{id}：删除（级联软删） */
export function deleteConversation(conversationId: string): Promise<void> {
  return del(`/conversations/${conversationId}`);
}

/** GET /conversations/{id}/messages：消息历史（ASC 正序） */
export function listMessages(conversationId: string): Promise<ChatMessage[]> {
  return get(`/conversations/${conversationId}/messages`);
}

/** POST /conversations/messages/{id}/feedback：提交点赞/踩（vote=1/-1） */
export function submitFeedback(messageId: string, vote: 1 | -1): Promise<void> {
  return post(`/conversations/messages/${messageId}/feedback`, { vote });
}

/** DELETE /conversations/messages/{id}/feedback：取消反馈 */
export function cancelFeedback(messageId: string): Promise<void> {
  return del(`/conversations/messages/${messageId}/feedback`);
}

/** POST /conversations/messages/{id}/recommended-questions：生成推荐追问 */
export function fetchRecommendedQuestions(messageId: string): Promise<RecommendedQuestionsPayload> {
  return post(`/conversations/messages/${messageId}/recommended-questions`);
}

/** POST /rag/v3/stop：停止流式任务（query 参数 snake_case task_id，对齐当前后端） */
export function stopGeneration(taskId: string): Promise<void> {
  return post("/rag/v3/stop", undefined, { params: { task_id: taskId } });
}
