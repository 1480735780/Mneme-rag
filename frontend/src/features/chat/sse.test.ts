// M1 #3 SSE 分帧解析单测：帧拆分 / 跨 chunk 缓冲 / 事件分发 / Bearer 头 / 401
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken, clearAuth } from "@/shared/auth/storage";

import { connectChatSSE } from "./sse";

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) {
        controller.enqueue(encoder.encode(c));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function mockLocation(pathname: string): void {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname, href: `http://localhost${pathname}` },
  });
}

function makeHandlers() {
  return {
    onMeta: vi.fn(),
    onDelta: vi.fn(),
    onFinish: vi.fn(),
    onCancel: vi.fn(),
    onReject: vi.fn(),
    onError: vi.fn(),
    onDone: vi.fn(),
  };
}

const frame = {
  meta: `event: meta\ndata: {"conversationId":"c1","taskId":"t1"}\n\n`,
  message: `event: message\ndata: {"type":"response","delta":"你好"}\n\n`,
  think: `event: message\ndata: {"type":"think","delta":"思考"}\n\n`,
  finish: `event: finish\ndata: {"messageId":"m1","sources":[],"messageStatus":"NORMAL"}\n\n`,
  done: `event: done\ndata: [DONE]\n\n`,
  reject: `event: reject\ndata: 系统繁忙，请稍后再试\n\n`,
  error: `event: error\ndata: boom\n\n`,
  cancel: `event: cancel\ndata: {"messageStatus":"INTERRUPTED"}\n\n`,
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  clearAuth();
  mockLocation("/chat");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function connect(chunks: string[], opts?: Partial<Parameters<typeof connectChatSSE>[0]>) {
  fetchMock.mockResolvedValue(streamResponse(chunks));
  const handlers = makeHandlers();
  const signal = new AbortController().signal;
  await connectChatSSE({
    question: "q",
    signal,
    ...handlers,
    ...opts,
  });
  return handlers;
}

describe("SSE 解析", () => {
  it("完整帧序列分发到各回调", async () => {
    const handlers = await connect([
      frame.meta,
      frame.think,
      frame.message,
      frame.finish,
      frame.done,
    ]);
    expect(handlers.onMeta).toHaveBeenCalledWith({ conversationId: "c1", taskId: "t1" });
    expect(handlers.onDelta).toHaveBeenCalledWith({ type: "think", delta: "思考" });
    expect(handlers.onDelta).toHaveBeenCalledWith({ type: "response", delta: "你好" });
    expect(handlers.onFinish).toHaveBeenCalledWith({
      messageId: "m1",
      sources: [],
      messageStatus: "NORMAL",
    });
    expect(handlers.onDone).toHaveBeenCalledTimes(1);
  });

  it("跨 chunk 拆帧仍正确缓冲解析", async () => {
    const all = frame.meta + frame.message + frame.done;
    // 从任意位置切开，模拟网络分包
    const cut1 = Math.floor(all.length / 2);
    const handlers = await connect([all.slice(0, cut1), all.slice(cut1)]);
    expect(handlers.onMeta).toHaveBeenCalledTimes(1);
    expect(handlers.onDelta).toHaveBeenCalledWith({ type: "response", delta: "你好" });
    expect(handlers.onDone).toHaveBeenCalledTimes(1);
  });

  it("reject / error / cancel 分发", async () => {
    const handlers = await connect([frame.reject, frame.error, frame.cancel]);
    expect(handlers.onReject).toHaveBeenCalledWith("系统繁忙，请稍后再试");
    expect(handlers.onError).toHaveBeenCalledWith("boom");
    expect(handlers.onCancel).toHaveBeenCalledWith({ messageStatus: "INTERRUPTED" });
  });

  it("携带 Bearer 请求头并带 snake_case 参数", async () => {
    setToken("tok1");
    fetchMock.mockResolvedValue(streamResponse([frame.done]));
    await connectChatSSE({
      question: "你好",
      conversationId: "c1",
      deepThinking: true,
      signal: new AbortController().signal,
      ...makeHandlers(),
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/rag/v3/chat");
    expect(url).toContain("question=%E4%BD%A0%E5%A5%BD");
    expect(url).toContain("conversation_id=c1");
    expect(url).toContain("deep_thinking=true");
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok1");
  });

  it("401 时清空凭据并跳登录页", async () => {
    setToken("expired");
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));
    await expect(
      connectChatSSE({
        question: "q",
        signal: new AbortController().signal,
        ...makeHandlers(),
      }),
    ).rejects.toMatchObject({ message: "登录已过期，请重新登录" });
    expect(window.location.href).toBe("/login");
  });

  it("HTTP 非 200 抛流式连接失败", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 500 }));
    await expect(
      connectChatSSE({ question: "q", signal: new AbortController().signal, ...makeHandlers() }),
    ).rejects.toMatchObject({ message: "流式连接失败 (HTTP 500)" });
  });
});
