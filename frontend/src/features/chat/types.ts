// M1 #1 聊天域类型（对齐后端 ConversationVO / ConversationMessageVO / SSE 协议）

export type Role = "user" | "assistant" | "system";

/** 持久化消息状态（后端 MessageStatus.name 大写枚举） */
export type PersistedMessageStatus = "NORMAL" | "INTERRUPTED" | "REJECTED";

/** 前端流式运行态 */
export type RuntimeMessageStatus = "streaming" | "done" | "cancelled" | "error";

export type FeedbackValue = "like" | "dislike" | null;

/** 来源引用（对齐后端 SourceRef.to_dict，camelCase） */
export interface SourceRef {
  index?: number;
  docId: string;
  docName?: string;
  sourceType?: string;
  fileType?: string | null;
  url?: string | null;
  excerpt?: string;
}

/** 会话（对齐 ConversationVO） */
export interface Conversation {
  conversationId: string;
  title: string;
  lastTime?: string;
}

/** 会话消息（对齐 ConversationMessageVO；尾部为前端流式运行时态） */
export interface ChatMessage {
  id: string;
  conversationId?: string;
  role: Role;
  content: string;
  thinkingContent?: string;
  thinkingDuration?: number;
  vote?: number | null;
  sources?: SourceRef[];
  recommendedQuestions?: string[];
  messageStatus?: PersistedMessageStatus;
  createTime?: string;
  // ---- 前端流式运行时态 ----
  status?: RuntimeMessageStatus;
  isDeepThinking?: boolean;
  recommendedState?: "loading" | "ready" | "error";
}

/** SSE meta 事件载荷 */
export interface StreamMetaPayload {
  conversationId: string;
  taskId: string;
}

/** SSE message 事件增量载荷 */
export interface MessageDeltaPayload {
  type: "think" | "response";
  delta: string;
}

/** SSE finish / cancel 事件载荷（对齐 CompletionPayload，messageStatus 必含） */
export interface CompletionPayload {
  messageId?: string | null;
  title?: string | null;
  sources?: SourceRef[] | null;
  messageStatus: PersistedMessageStatus;
}

/** 推荐追问响应（对齐后端 RecommendedQuestionsPayload {status, questions}） */
export interface RecommendedQuestionsPayload {
  status: "SUCCESS" | "EMPTY" | "FAILED";
  questions: string[];
}
