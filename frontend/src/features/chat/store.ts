// M1 #2 聊天 Zustand store
// 会话列表 + 当前消息流 + SSE 流式状态；SSE 连接为纯回调模块（store → sse，无循环依赖）
import { create } from "zustand";
import { toast } from "sonner";

import * as chatApi from "./api";
import { connectChatSSE } from "./sse";
import type {
  ChatMessage,
  CompletionPayload,
  Conversation,
  MessageDeltaPayload,
} from "./types";

interface ChatState {
  conversations: Conversation[];
  conversationsLoading: boolean;
  activeId: string | null;
  messages: ChatMessage[];
  messagesLoading: boolean;
  isStreaming: boolean;
  streamTaskId: string | null;
  streamAbort: AbortController | null;
  error: string | null;
  lastQuestion: string | null;

  loadConversations: () => Promise<void>;
  createConversation: () => void;
  selectConversation: (id: string | null) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
  removeConversation: (id: string) => Promise<void>;
  sendMessage: (text: string, deepThinking?: boolean) => Promise<void>;
  stopGeneration: () => Promise<void>;
  submitFeedback: (messageId: string, vote: 1 | -1) => Promise<void>;
  cancelFeedback: (messageId: string) => Promise<void>;
  loadRecommendedQuestions: (messageId: string) => Promise<void>;
  resetChat: () => void;
}

let tempSeq = 0;
function tempId(prefix: string): string {
  tempSeq += 1;
  return `${prefix}-${Date.now()}-${tempSeq}`;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  conversationsLoading: false,
  activeId: null,
  messages: [],
  messagesLoading: false,
  isStreaming: false,
  streamTaskId: null,
  streamAbort: null,
  error: null,
  lastQuestion: null,

  loadConversations: async () => {
    set({ conversationsLoading: true });
    try {
      const conversations = await chatApi.listConversations();
      set({ conversations });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载会话失败");
    } finally {
      set({ conversationsLoading: false });
    }
  },

  createConversation: () => {
    void get().stopGeneration();
    set({ activeId: null, messages: [], error: null });
  },

  selectConversation: async (id) => {
    if (id === get().activeId) return;
    void get().stopGeneration();
    if (id === null) {
      set({ activeId: null, messages: [], error: null });
      return;
    }
    set({ activeId: id, messages: [], messagesLoading: true, error: null });
    try {
      const messages = await chatApi.listMessages(id);
      set({ messages: normalizeMessages(messages) });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : "加载消息失败" });
    } finally {
      set({ messagesLoading: false });
    }
  },

  renameConversation: async (id, title) => {
    try {
      await chatApi.renameConversation(id, title);
      set({
        conversations: get().conversations.map((c) =>
          c.conversationId === id ? { ...c, title } : c,
        ),
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重命名失败");
    }
  },

  removeConversation: async (id) => {
    try {
      await chatApi.deleteConversation(id);
      const isActive = get().activeId === id;
      set({
        conversations: get().conversations.filter((c) => c.conversationId !== id),
        ...(isActive ? { activeId: null, messages: [] } : {}),
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  },

  sendMessage: async (text, deepThinking = false) => {
    const { streamAbort, activeId } = get();
    if (streamAbort) return; // 正在生成，忽略重复发送

    const abort = new AbortController();
    const userMessage: ChatMessage = { id: tempId("u"), role: "user", content: text };
    const assistantMessage: ChatMessage = {
      id: tempId("ai"),
      role: "assistant",
      content: "",
      thinkingContent: "",
      sources: [],
      recommendedQuestions: [],
      status: "streaming",
      isDeepThinking: deepThinking,
    };
    set({
      isStreaming: true,
      streamAbort: abort,
      streamTaskId: null,
      error: null,
      messages: [...get().messages, userMessage, assistantMessage],
    });

    try {
      await connectChatSSE({
        question: text,
        conversationId: activeId ?? undefined,
        deepThinking,
        signal: abort.signal,
        onMeta: ({ conversationId, taskId }) => {
          set({ activeId: conversationId, streamTaskId: taskId });
        },
        onDelta: (payload) => {
          set({ messages: appendDelta(get().messages, payload) });
        },
        onFinish: (payload) => {
          set({ messages: completeMessage(get().messages, payload) });
          upsertConversation(get, set, payload);
        },
        onCancel: (payload) => {
          set({
            messages: completeMessage(get().messages, payload, "cancelled"),
            isStreaming: false,
            streamAbort: null,
          });
        },
        onReject: (message) => {
          set({ error: message, messages: failAssistant(get().messages) });
        },
        onError: (message) => {
          set({ error: message, messages: failAssistant(get().messages) });
        },
        onDone: () => {
          set({ isStreaming: false, streamAbort: null });
        },
      });
    } catch (err) {
      if (!abort.signal.aborted) {
        const message = err instanceof Error ? err.message : "网络错误，请稍后重试";
        set({ error: message, isStreaming: false, streamAbort: null, messages: failAssistant(get().messages) });
      }
    }
  },

  stopGeneration: async () => {
    const { streamAbort, streamTaskId, isStreaming } = get();
    if (!isStreaming || !streamAbort) return;
    streamAbort.abort(); // 停止读取 SSE
    if (streamTaskId) {
      chatApi.stopGeneration(streamTaskId).catch(() => {});
    }
    set({ isStreaming: false, streamAbort: null });
  },

  submitFeedback: async (messageId, vote) => {
    try {
      await chatApi.submitFeedback(messageId, vote);
      set({
        messages: get().messages.map((m) => (m.id === messageId ? { ...m, vote } : m)),
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "反馈失败");
    }
  },

  cancelFeedback: async (messageId) => {
    try {
      await chatApi.cancelFeedback(messageId);
      set({
        messages: get().messages.map((m) => (m.id === messageId ? { ...m, vote: null } : m)),
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "取消反馈失败");
    }
  },

  loadRecommendedQuestions: async (messageId) => {
    set({
      messages: get().messages.map((m) =>
        m.id === messageId ? { ...m, recommendedState: "loading" } : m,
      ),
    });
    try {
      const payload = await chatApi.fetchRecommendedQuestions(messageId);
      set({
        messages: get().messages.map((m) =>
          m.id === messageId
            ? { ...m, recommendedQuestions: payload.questions, recommendedState: "ready" }
            : m,
        ),
      });
    } catch {
      set({
        messages: get().messages.map((m) =>
          m.id === messageId ? { ...m, recommendedState: "error" } : m,
        ),
      });
    }
  },

  resetChat: () => {
    get().streamAbort?.abort();
    set({
      conversations: [],
      conversationsLoading: false,
      activeId: null,
      messages: [],
      messagesLoading: false,
      isStreaming: false,
      streamTaskId: null,
      streamAbort: null,
      error: null,
      lastQuestion: null,
    });
  },
}));

// ==================== 纯函数辅助（可单测） ====================

function normalizeMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((m) => ({
    ...m,
    status: "done",
    sources: m.sources ?? [],
    recommendedQuestions: m.recommendedQuestions ?? [],
  }));
}

/** 把增量追加到最后一条 assistant 消息（不可变更新） */
export function appendDelta(messages: ChatMessage[], payload: MessageDeltaPayload): ChatMessage[] {
  const idx = messages.length - 1;
  const last = messages[idx];
  if (!last || last.role !== "assistant") return messages;
  const next = { ...last };
  if (payload.type === "think") {
    next.thinkingContent = (last.thinkingContent ?? "") + payload.delta;
  } else {
    next.content = last.content + payload.delta;
  }
  return [...messages.slice(0, idx), next];
}

/** finish/cancel：给最后一条 assistant 消息补 id/sources/status */
export function completeMessage(
  messages: ChatMessage[],
  payload: CompletionPayload,
  status: "done" | "cancelled" = "done",
): ChatMessage[] {
  const idx = messages.length - 1;
  const last = messages[idx];
  if (!last || last.role !== "assistant") return messages;
  const next: ChatMessage = {
    ...last,
    id: payload.messageId ?? last.id,
    status,
    sources: payload.sources ?? last.sources ?? [],
    messageStatus: payload.messageStatus,
  };
  return [...messages.slice(0, idx), next];
}

/** 把最后一条 assistant 消息标记为 error（错误/拒绝兜底） */
export function failAssistant(messages: ChatMessage[]): ChatMessage[] {
  const idx = messages.length - 1;
  const last = messages[idx];
  if (!last || last.role !== "assistant") return messages;
  return [...messages.slice(0, idx), { ...last, status: "error" }];
}

/** finish 携带标题时，把新会话插入列表 / 更新已有会话（首问后侧栏即时可见） */
function upsertConversation(
  get: () => ChatState,
  set: (partial: Partial<ChatState>) => void,
  payload: CompletionPayload,
): void {
  const activeId = get().activeId;
  if (!activeId || !payload.title) return;
  const now = new Date().toISOString();
  const exists = get().conversations.some((c) => c.conversationId === activeId);
  if (!exists) {
    set({
      conversations: [{ conversationId: activeId, title: payload.title, lastTime: now }, ...get().conversations],
    });
  } else {
    set({
      conversations: get().conversations.map((c) =>
        c.conversationId === activeId ? { ...c, title: payload.title ?? c.title, lastTime: now } : c,
      ),
    });
  }
}
