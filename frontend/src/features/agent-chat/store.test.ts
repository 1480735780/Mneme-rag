// v1.1 P2 agent-chat store 单测：纯函数 + 流式 action 状态机
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as agentApi from "./api";
import { connectAgentSSE } from "./sse";
import {
  appendBlockText,
  applyToolProgress,
  groupSessions,
  replayElapsed,
  sealOpenBlock,
  settleBlocks,
  upsertSession,
  useAgentChatStore,
} from "./store";
import type { AgentBlockUI, AgentMessage } from "./types";

vi.mock("./api", () => ({
  listAgentSessions: vi.fn(),
  listAgentMessages: vi.fn(),
  renameAgentSession: vi.fn(),
  deleteAgentSession: vi.fn(),
  batchDeleteAgentSessions: vi.fn(),
  getAgentMeta: vi.fn(),
  stopAgentTask: vi.fn(() => Promise.resolve()),
}));

vi.mock("./sse", () => ({
  connectAgentSSE: vi.fn(),
}));

const mockedApi = vi.mocked(agentApi);
const mockedConnect = vi.mocked(connectAgentSSE);

function streamMessage(id = "ai1", blocks: AgentBlockUI[] = []): AgentMessage {
  return { id, role: "assistant", content: "", blocks, status: "streaming" };
}

beforeEach(() => {
  useAgentChatStore.setState({
    sessions: [],
    currentSessionId: null,
    messages: [],
    isLoading: false,
    sessionsLoaded: false,
    inputFocusKey: 0,
    draft: null,
    isStreaming: false,
    isCreatingNew: false,
    streamTaskId: null,
    streamAbort: null,
    streamingMessageId: null,
    streamOpenBlockId: null,
    cancelRequested: false,
    frames: [],
  });
  vi.clearAllMocks();
});

describe("纯函数", () => {
  it("appendBlockText 敞开块同类追加", () => {
    const open: AgentBlockUI = { id: 1, kind: "answer", at: "09:00:00", text: "你" };
    const { messages, openBlockId } = appendBlockText(
      [streamMessage("ai1", [open])],
      "ai1",
      1,
      "answer",
      "好",
    );
    expect(openBlockId).toBe(1);
    expect((messages[0].blocks?.[0].text) === "你好").toBe(true);
    expect(messages[0].content).toBe("好");
  });

  it("appendBlockText 换段封口旧块并新开（answer → reasoning）", () => {
    const open: AgentBlockUI = { id: 501, kind: "answer", at: "09:00:00", text: "答" };
    const { messages, openBlockId } = appendBlockText(
      [streamMessage("ai1", [open])],
      "ai1",
      501,
      "reasoning",
      "想",
    );
    const blocks = messages[0].blocks ?? [];
    expect(openBlockId).toBe(blocks[1].id); // 新开块成为敞开块
    expect(blocks).toHaveLength(2);
    expect(blocks[0].kind).toBe("answer");
    expect(blocks[1].kind).toBe("reasoning");
    expect(blocks[1].open).toBe(true); // 思考块流式自动展开
    expect(messages[0].thinking).toBe("想");
  });

  it("appendBlockText 取消/错误态不再写入", () => {
    const cancelled: AgentMessage = {
      id: "ai1",
      role: "assistant",
      content: "",
      blocks: [],
      status: "cancelled",
    };
    const { messages } = appendBlockText([cancelled], "ai1", null, "answer", "x");
    expect(messages[0].content).toBe("");
  });

  it("sealOpenBlock 封口补耗时 思考块自动折叠", () => {
    const block: AgentBlockUI = {
      id: 1,
      kind: "reasoning",
      at: "09:00:00",
      text: "t",
      startMs: Date.now() - 50,
      open: true,
    };
    const sealed = sealOpenBlock([block], 1);
    expect(sealed[0].durationMs).toBeGreaterThanOrEqual(0);
    expect(sealed[0].open).toBe(false);
    // 非敞开块原样
    expect(sealOpenBlock([block], 2)[0].durationMs).toBeUndefined();
  });

  it("settleBlocks 残留 running 工具落终态", () => {
    const running: AgentBlockUI = {
      id: 1,
      kind: "tool",
      at: "09:00:00",
      name: "search_knowledge",
      status: "running",
      startMs: Date.now() - 10,
    };
    const settled = settleBlocks([running], "interrupted");
    expect(settled?.[0].status).toBe("interrupted");
    expect(settled?.[0].durationMs).toBeGreaterThanOrEqual(0);
  });

  it("applyToolProgress start 追加 running 块并封口文本块", () => {
    const open: AgentBlockUI = { id: 1, kind: "answer", at: "09:00:00", text: "答" };
    const { messages, openBlockId } = applyToolProgress(
      [streamMessage("ai1", [open])],
      "ai1",
      1,
      { name: "search_knowledge", displayName: "知识库检索", status: "start" },
    );
    expect(openBlockId).toBeNull();
    const blocks = messages[0].blocks ?? [];
    expect(blocks).toHaveLength(2);
    expect(blocks[1].status).toBe("running");
    expect(blocks[1].displayName).toBe("知识库检索");
  });

  it("applyToolProgress end 按名字后进先出闭合同名 running 块", () => {
    const t1: AgentBlockUI = {
      id: 1,
      kind: "tool",
      at: "09:00:00",
      name: "search_knowledge",
      status: "running",
      startMs: Date.now() - 10,
    };
    const t2: AgentBlockUI = {
      id: 2,
      kind: "tool",
      at: "09:00:01",
      name: "get_weather",
      status: "running",
      startMs: Date.now() - 5,
    };
    const { messages } = applyToolProgress([streamMessage("ai1", [t1, t2])], "ai1", null, {
      name: "get_weather",
      status: "end",
      result: "晴 25C",
      ok: true,
    });
    const blocks = messages[0].blocks ?? [];
    expect(blocks[0].status).toBe("running"); // 同名不匹配的不动
    expect(blocks[1].status).toBe("done");
    expect(blocks[1].result).toBe("晴 25C");
  });

  it("applyToolProgress end ok=false 判失败", () => {
    const t1: AgentBlockUI = {
      id: 1,
      kind: "tool",
      at: "09:00:00",
      name: "mcp_tool",
      status: "running",
    };
    const { messages } = applyToolProgress([streamMessage("ai1", [t1])], "ai1", null, {
      name: "mcp_tool",
      status: "end",
      result: "boom",
      ok: false,
    });
    expect((messages[0].blocks?.[0].status) === "failed").toBe(true);
  });

  it("upsertSession 已有则合并 无则插头 lastTime 倒序", () => {
    const base = [
      { id: "a", title: "A", lastTime: "2026-08-30T10:00:00" },
      { id: "b", title: "B", lastTime: "2026-08-29T10:00:00" },
    ];
    const merged = upsertSession(base, { id: "b", title: "B2", lastTime: "2026-08-31T10:00:00" });
    expect(merged.map((s) => s.id)).toEqual(["b", "a"]);
    expect(merged[0].title).toBe("B2");
    const inserted = upsertSession(base, { id: "c", title: "C", lastTime: "2026-09-01T10:00:00" });
    expect(inserted[0].id).toBe("c");
  });

  it("groupSessions 按最近程度分桶 无时间落更早", () => {
    const now = new Date();
    const iso = (offsetMs: number) => new Date(now.getTime() - offsetMs).toISOString();
    const groups = groupSessions([
      { id: "a", title: "A", lastTime: iso(1000) },
      { id: "b", title: "B", lastTime: iso(2 * 864e5) },
      { id: "c", title: "C", lastTime: iso(30 * 864e5) },
      { id: "d", title: "D" },
    ]);
    expect(groups.map((g) => g.label)).toEqual(["今天", "7 天内", "更早"]);
    expect(groups[2].items.map((s) => s.id)).toEqual(["c", "d"]);
  });

  it("replayElapsed 配对差值 非法/倒挂返回 undefined", () => {
    expect(replayElapsed("2026-08-30T09:00:00", "2026-08-30T09:00:05")).toBe(5000);
    expect(replayElapsed("2026-08-30T09:00:05", "2026-08-30T09:00:00")).toBeUndefined();
    expect(replayElapsed(undefined, "2026-08-30T09:00:00")).toBeUndefined();
  });
});

describe("sendMessage 状态机", () => {
  it("meta 落会话与 taskId（首问插新会话）", async () => {
    let capture: Parameters<typeof connectAgentSSE>[0] | null = null;
    mockedConnect.mockImplementation(async (options) => {
      capture = options;
      options.onMeta({ conversationId: "c1", taskId: "t1" });
      options.onDelta({ type: "response", delta: "答" });
      options.onFinish({ messageId: "m1", title: "第一问", messageStatus: "NORMAL" });
      options.onDone();
    });

    await useAgentChatStore.getState().sendMessage("第一问");
    expect(useAgentChatStore.getState().currentSessionId).toBe("c1");
    expect(useAgentChatStore.getState().streamTaskId).toBeNull(); // done 后清空
    expect(useAgentChatStore.getState().isStreaming).toBe(false);
    // 连接参数：snake_case conversation_id 不带（新会话）
    const options = capture as unknown as { question: string; conversationId?: string };
    expect(options.question).toBe("第一问");
    expect(options.conversationId).toBeUndefined();

    const msgs = useAgentChatStore.getState().messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[1].id).toBe("m1");
    expect(msgs[1].content).toBe("答");
    expect(msgs[1].status).toBe("done");
    expect(useAgentChatStore.getState().sessions[0].id).toBe("c1");
    expect(useAgentChatStore.getState().sessions[0].turns).toBe(1);
  });

  it("finish 补 title 与轮数 已有会话标题以 finish 为准", async () => {
    useAgentChatStore.setState({
      currentSessionId: "c1",
      sessions: [{ id: "c1", title: "旧题" }],
    });
    mockedConnect.mockImplementation(async (options) => {
      options.onMeta({ conversationId: "c1", taskId: "t1" });
      options.onFinish({ messageId: "m1", title: "新题", messageStatus: "NORMAL" });
      options.onDone();
    });
    await useAgentChatStore.getState().sendMessage("问");
    expect(useAgentChatStore.getState().sessions[0].title).toBe("新题");
  });

  it("onEvent 全帧进原始帧抽屉", async () => {
    mockedConnect.mockImplementation(async (options) => {
      options.onEvent?.("meta", { conversationId: "c1", taskId: "t1" });
      options.onEvent?.("message", { type: "response", delta: "x" });
      options.onDone();
    });
    await useAgentChatStore.getState().sendMessage("问");
    expect(useAgentChatStore.getState().frames.map((f) => f.name)).toEqual(["meta", "message"]);
  });

  it("onCancel 落 INTERRUPTED 并补（已停止生成）", async () => {
    mockedConnect.mockImplementation(async (options) => {
      options.onMeta({ conversationId: "c1", taskId: "t1" });
      options.onDelta({ type: "response", delta: "部分" });
      options.onCancel({ messageId: "m1", messageStatus: "INTERRUPTED" });
      options.onDone();
    });
    await useAgentChatStore.getState().sendMessage("问");
    const msgs = useAgentChatStore.getState().messages;
    expect(msgs[1].status).toBe("cancelled");
    expect(msgs[1].messageStatus).toBe("INTERRUPTED");
    expect(msgs[1].content).toContain("（已停止生成）");
    expect(useAgentChatStore.getState().isStreaming).toBe(false);
  });

  it("error 帧 → 消息 error + 流态清空", async () => {
    mockedConnect.mockImplementation(async (options) => {
      options.onMeta({ conversationId: "c1", taskId: "t1" });
      options.onError("引擎炸了");
      options.onDone();
    });
    await useAgentChatStore.getState().sendMessage("问");
    const msgs = useAgentChatStore.getState().messages;
    expect(msgs[1].status).toBe("error");
    expect(useAgentChatStore.getState().isStreaming).toBe(false);
  });

  it("非事件流拒绝（业务错）→ 抛 ApiError 落 error 态", async () => {
    const { ApiError } = await import("@/shared/types/api");
    mockedConnect.mockImplementation(async () => {
      throw new ApiError("当前会话处理中，请稍后再发起新的对话", {});
    });
    await useAgentChatStore.getState().sendMessage("问");
    const state = useAgentChatStore.getState();
    expect(state.messages[1].status).toBe("error");
    expect(state.isStreaming).toBe(false);
    expect(state.streamingMessageId).toBeNull();
  });

  it("流式中重复发送被忽略", async () => {
    let release: (() => void) | undefined;
    mockedConnect.mockImplementation(
      async () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const first = useAgentChatStore.getState().sendMessage("问一");
    await Promise.resolve();
    await useAgentChatStore.getState().sendMessage("问二");
    // 第二次发送被忽略：仍只有一组消息
    expect(useAgentChatStore.getState().messages.filter((m) => m.role === "user")).toHaveLength(1);
    release?.();
    await first;
  });
});

describe("cancelGeneration / stopGeneration", () => {
  it("cancelRequested 先置位 无 taskId 则不发停止（meta 后补发由 sendMessage 内承接）", () => {
    useAgentChatStore.setState({ isStreaming: true, streamingMessageId: "ai1" });
    useAgentChatStore.getState().cancelGeneration();
    expect(useAgentChatStore.getState().cancelRequested).toBe(true);
    expect(mockedApi.stopAgentTask).not.toHaveBeenCalled();
  });

  it("有 taskId 时调 stopAgentTask（snake_case 由 api 层承担）", () => {
    useAgentChatStore.setState({ isStreaming: true, streamTaskId: "t9" });
    useAgentChatStore.getState().cancelGeneration();
    expect(mockedApi.stopAgentTask).toHaveBeenCalledWith("t9");
  });

  it("stopGeneration 硬中断：abort + 停止接口 + 流态清空", async () => {
    const abort = new AbortController();
    useAgentChatStore.setState({ isStreaming: true, streamTaskId: "t1", streamAbort: abort });
    await useAgentChatStore.getState().stopGeneration();
    expect(abort.signal.aborted).toBe(true);
    expect(mockedApi.stopAgentTask).toHaveBeenCalledWith("t1");
    expect(useAgentChatStore.getState().isStreaming).toBe(false);
  });
});

describe("loadMessages 回放", () => {
  it("blocks 回放为 UI 块（思考折叠 工具默认 done）", async () => {
    mockedApi.listAgentMessages.mockResolvedValue([
      { id: "u1", role: "user", content: "问", createTime: "2026-08-30T09:00:00" },
      {
        id: "a1",
        role: "assistant",
        content: "答",
        thinkingContent: "想",
        blocks: [
          { kind: "reasoning", at: "2026-08-30T09:00:01", text: "想" },
          { kind: "tool", at: "2026-08-30T09:00:02", name: "search_knowledge", result: "r" },
          { kind: "answer", at: "2026-08-30T09:00:03", text: "答" },
        ],
        messageStatus: "NORMAL",
        createTime: "2026-08-30T09:00:03",
      },
    ]);
    await useAgentChatStore.getState().loadMessages("c1");
    const msgs = useAgentChatStore.getState().messages;
    expect(msgs).toHaveLength(2);
    const blocks = msgs[1].blocks ?? [];
    expect(blocks.map((b) => b.kind)).toEqual(["reasoning", "tool", "answer"]);
    expect(blocks[0].open).toBe(false);
    expect(blocks[1].status).toBe("done");
    expect(msgs[1].elapsedMs).toBe(3000); // 回放差值 09:00:00 → 09:00:03
  });

  it("旧数据无块结构：按 thinking/content 合成", async () => {
    mockedApi.listAgentMessages.mockResolvedValue([
      { id: "a1", role: "assistant", content: "答", thinkingContent: "想", createTime: "2026-08-30T09:00:00" },
    ]);
    await useAgentChatStore.getState().loadMessages("c1");
    const blocks = useAgentChatStore.getState().messages[0].blocks ?? [];
    expect(blocks.map((b) => b.kind)).toEqual(["reasoning", "answer"]);
  });
});
