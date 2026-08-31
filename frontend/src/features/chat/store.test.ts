// M1 #2 store 单测：纯函数 + 流式 action 状态机
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as chatApi from "./api";
import { connectChatSSE } from "./sse";
import { appendDelta, completeMessage, failAssistant, useChatStore } from "./store";
import type { ChatMessage } from "./types";

vi.mock("./api", () => ({
  listConversations: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listMessages: vi.fn(),
  submitFeedback: vi.fn(),
  cancelFeedback: vi.fn(),
  fetchRecommendedQuestions: vi.fn(),
  stopGeneration: vi.fn(),
}));

vi.mock("./sse", () => ({
  connectChatSSE: vi.fn(),
}));

const mockedApi = vi.mocked(chatApi);
const mockedConnect = vi.mocked(connectChatSSE);

function baseMessages(): ChatMessage[] {
  return [
    { id: "u1", role: "user", content: "你好" },
    { id: "ai1", role: "assistant", content: "", thinkingContent: "", status: "streaming" },
  ];
}

beforeEach(() => {
  useChatStore.setState({
    conversations: [],
    conversationsLoading: false,
    activeId: null,
    messages: [],
    messagesLoading: false,
    isStreaming: false,
    streamTaskId: null,
    streamAbort: null,
    error: null,
  });
  vi.clearAllMocks();
});

describe("纯函数", () => {
  it("appendDelta 追加 response / think", () => {
    let msgs = appendDelta(baseMessages(), { type: "response", delta: "你" });
    msgs = appendDelta(msgs, { type: "response", delta: "好" });
    msgs = appendDelta(msgs, { type: "think", delta: "思考" });
    expect(msgs[1].content).toBe("你好");
    expect(msgs[1].thinkingContent).toBe("思考");
  });

  it("completeMessage 补 id/sources/status", () => {
    const msgs = completeMessage(baseMessages(), {
      messageId: "m1",
      sources: [{ docId: "d1", index: 1 }],
      messageStatus: "NORMAL",
    });
    expect(msgs[1]).toMatchObject({ id: "m1", status: "done" });
    expect(msgs[1].sources).toEqual([{ docId: "d1", index: 1 }]);
  });

  it("failAssistant 标记 error", () => {
    const msgs = failAssistant(baseMessages());
    expect(msgs[1].status).toBe("error");
  });
});

describe("sendMessage 流式状态机", () => {
  it("meta→delta→finish→done 完整流转", async () => {
    mockedConnect.mockImplementation(async (opts) => {
      opts.onMeta({ conversationId: "c1", taskId: "t1" });
      opts.onDelta({ type: "think", delta: "思" });
      opts.onDelta({ type: "response", delta: "你好" });
      opts.onFinish({ messageId: "m1", sources: [], messageStatus: "NORMAL" });
      opts.onDone();
    });

    await useChatStore.getState().sendMessage("你好");

    const s = useChatStore.getState();
    expect(s.activeId).toBe("c1");
    expect(s.streamTaskId).toBe("t1");
    expect(s.isStreaming).toBe(false);
    expect(s.messages).toHaveLength(2);
    expect(s.messages[0].role).toBe("user");
    expect(s.messages[0].content).toBe("你好");
    expect(s.messages[1]).toMatchObject({
      role: "assistant",
      content: "你好",
      thinkingContent: "思",
      id: "m1",
      status: "done",
    });
    expect(mockedConnect).toHaveBeenCalledWith(
      expect.objectContaining({ question: "你好", conversationId: undefined, deepThinking: false }),
    );
  });

  it("cancel 事件标记 cancelled 并停止流", async () => {
    mockedConnect.mockImplementation(async (opts) => {
      opts.onMeta({ conversationId: "c1", taskId: "t1" });
      opts.onDelta({ type: "response", delta: "部分" });
      opts.onCancel({ messageId: "m2", messageStatus: "INTERRUPTED" });
    });

    await useChatStore.getState().sendMessage("hi");
    const s = useChatStore.getState();
    expect(s.messages[1]).toMatchObject({ content: "部分", status: "cancelled" });
    expect(s.isStreaming).toBe(false);
  });

  it("reject 事件展示拒绝文案并标记 error", async () => {
    mockedConnect.mockImplementation(async (opts) => {
      opts.onReject("系统繁忙，请稍后再试");
    });
    await useChatStore.getState().sendMessage("hi");
    const s = useChatStore.getState();
    expect(s.error).toBe("系统繁忙，请稍后再试");
    expect(s.messages[1].status).toBe("error");
  });

  it("连接异常时兜底 error", async () => {
    mockedConnect.mockRejectedValue(new Error("网络错误，无法连接流式服务"));
    await useChatStore.getState().sendMessage("hi");
    const s = useChatStore.getState();
    expect(s.isStreaming).toBe(false);
    expect(s.error).toBe("网络错误，无法连接流式服务");
    expect(s.messages[1].status).toBe("error");
  });

  it("流式中重复发送被忽略", async () => {
    useChatStore.setState({ streamAbort: new AbortController(), isStreaming: true });
    await useChatStore.getState().sendMessage("again");
    expect(mockedConnect).not.toHaveBeenCalled();
  });
});

describe("stopGeneration", () => {
  it("abort fetch 并调用后端 stop", async () => {
    mockedApi.stopGeneration.mockResolvedValue(undefined);
    const abort = new AbortController();
    useChatStore.setState({ isStreaming: true, streamAbort: abort, streamTaskId: "t9" });
    const abortSpy = vi.spyOn(abort, "abort");
    await useChatStore.getState().stopGeneration();
    expect(abortSpy).toHaveBeenCalled();
    expect(mockedApi.stopGeneration).toHaveBeenCalledWith("t9");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });
});

describe("会话操作", () => {
  it("selectConversation 加载消息历史", async () => {
    mockedApi.listMessages.mockResolvedValue([
      { id: "m1", role: "user", content: "q", conversationId: "c1" },
      { id: "m2", role: "assistant", content: "a", sources: [], recommendedQuestions: [], conversationId: "c1" },
    ]);
    await useChatStore.getState().selectConversation("c1");
    const s = useChatStore.getState();
    expect(s.activeId).toBe("c1");
    expect(s.messages).toHaveLength(2);
    expect(s.messages[0].status).toBe("done");
  });

  it("submitFeedback 更新 vote", async () => {
    mockedApi.submitFeedback.mockResolvedValue(undefined);
    useChatStore.setState({ messages: [{ id: "m1", role: "assistant", content: "a" }] });
    await useChatStore.getState().submitFeedback("m1", 1);
    expect(useChatStore.getState().messages[0].vote).toBe(1);
    expect(mockedApi.submitFeedback).toHaveBeenCalledWith("m1", 1);
  });

  it("removeConversation 删除并清空当前", async () => {
    mockedApi.deleteConversation.mockResolvedValue(undefined);
    useChatStore.setState({
      conversations: [
        { conversationId: "c1", title: "a" },
        { conversationId: "c2", title: "b" },
      ],
      activeId: "c1",
      messages: [{ id: "m1", role: "user", content: "x" }],
    });
    await useChatStore.getState().removeConversation("c1");
    const s = useChatStore.getState();
    expect(s.conversations.map((c) => c.conversationId)).toEqual(["c2"]);
    expect(s.activeId).toBeNull();
    expect(s.messages).toEqual([]);
  });

  it("resetChat 清空全部状态", () => {
    useChatStore.setState({ activeId: "c1", isStreaming: true, messages: [{ id: "x", role: "user", content: "y" }] });
    useChatStore.getState().resetChat();
    const s = useChatStore.getState();
    expect(s.activeId).toBeNull();
    expect(s.messages).toEqual([]);
    expect(s.isStreaming).toBe(false);
  });
});
