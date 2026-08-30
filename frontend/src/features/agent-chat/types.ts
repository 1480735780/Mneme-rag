// v1.1 P2 Agent 对话域类型（对齐后端 agent/controller.py 协议 + ragent-new types/agent.ts）

export type AgentRole = "user" | "assistant";

/** 前端流式运行态 */
export type AgentMessageUiStatus = "streaming" | "done" | "cancelled" | "error";

/** 持久化消息状态（后端 AgentMessageStatus.name） */
export type AgentPersistedMessageStatus = "NORMAL" | "INTERRUPTED";

// hint 为流式过程中的运行提示 只存在于前端时间线 后端不落库
export type AgentBlockKind = "reasoning" | "answer" | "tool" | "hint";

/** 会话（对齐 AgentConversationVO） */
export interface AgentSession {
  id: string;
  title: string;
  lastTime?: string;
  turns?: number;
}

// 后端回放的时间线块（对齐 AgentBlock.to_dict camelCase）
export interface AgentBlock {
  kind: AgentBlockKind;
  at: string;
  text?: string | null;
  name?: string | null;
  displayName?: string | null;
  status?: "done" | "interrupted" | null;
  result?: string | null;
}

// 前端时间线块 id 为客户端自增 open 为折叠展开态
export interface AgentBlockUI {
  id: number;
  kind: AgentBlockKind;
  at: string;
  text?: string;
  name?: string;
  displayName?: string;
  status?: "running" | "done" | "failed" | "interrupted";
  result?: string;
  open?: boolean;
  // 流式实测耗时 仅本次连接内可得 回放块无此二字段 行级不显示耗时
  startMs?: number;
  durationMs?: number;
}

export interface AgentMessage {
  id: string;
  role: AgentRole;
  content: string;
  thinking?: string;
  blocks?: AgentBlockUI[];
  status?: AgentMessageUiStatus;
  messageStatus?: AgentPersistedMessageStatus;
  createdAt?: string;
  // 轮次总耗时 流式收尾实测 回放由相邻 user/assistant createTime 差值补齐
  elapsedMs?: number;
}

// ---- SSE 七类帧载荷（对齐 AgentSSEEventType + 五类 Payload camelCase）----

export interface AgentMetaPayload {
  conversationId: string;
  taskId: string;
}

export interface AgentMessageDelta {
  type: "think" | "response";
  delta: string;
}

export interface AgentToolProgress {
  name: string;
  displayName?: string;
  status?: "start" | "end";
  result?: string | null;
  ok?: boolean | null;
}

export interface AgentHintPayload {
  code: string;
  text: string;
}

export interface AgentCompletionPayload {
  messageId?: string | null;
  title?: string | null;
  messageStatus?: AgentPersistedMessageStatus;
}

// 引擎探活身份 GET /agent/v1/meta
export interface AgentEngineMeta {
  framework: string;
  model: string;
  maxIters: number;
  capabilities: string[];
  toolProvider: string;
  mcpConfigured: boolean;
}

// 原始帧抽屉逐条记录
export interface AgentRawFrame {
  id: number;
  ts: string;
  name: string;
  data: unknown;
}
