// v1.1 P2 agent-chat sse 单测：帧解析与七类事件分发 + 业务拒绝 + 401
import { afterEach, describe, expect, it, vi } from "vitest";

import { connectAgentSSE, type AgentSSEHandlers } from "./sse";

function sseResponse(chunks: string[], contentType = "text/event-stream"): Response {
  const encoder = new TextEncoder();
  let sent = 0;
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": contentType }),
    body: {
      getReader() {
        return {
          read: async () => {
            if (sent < chunks.length) {
              const value = encoder.encode(chunks[sent]);
              sent += 1;
              return { done: false, value };
            }
            return { done: true, value: undefined };
          },
          cancel: vi.fn(),
        };
      },
    },
  } as unknown as Response;
}

function baseHandlers(): AgentSSEHandlers & Record<string, ReturnType<typeof vi.fn>> {
  return {
    onMeta: vi.fn(),
    onDelta: vi.fn(),
    onTool: vi.fn(),
    onHint: vi.fn(),
    onFinish: vi.fn(),
    onCancel: vi.fn(),
    onError: vi.fn(),
    onDone: vi.fn(),
    onEvent: vi.fn(),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("connectAgentSSE", () => {
  it("七类帧按事件名分发（跨 chunk 分帧）", async () => {
    const handlers = baseHandlers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: meta\ndata: {"conversationId":"c1","taskId":"t1"}\n\nev',
          'ent: message\ndata: {"type":"think","delta":"想"}\n\nevent: tool\ndata: {"name":"search_knowledge","status":"start"}\n\n',
          'event: hint\ndata: {"code":"EXCEED_MAX_ITERS","text":"达到上限"}\n\nevent: finish\ndata: {"messageId":"m1"}\n\n',
          'event: cancel\ndata: {"messageStatus":"INTERRUPTED"}\n\nevent: error\ndata: {"error":"boom"}\n\nevent: done\ndata: [DONE]\n\n',
        ]),
      ),
    );

    await connectAgentSSE({ question: "问", signal: new AbortController().signal, ...handlers });

    expect(handlers.onMeta).toHaveBeenCalledWith({ conversationId: "c1", taskId: "t1" });
    expect(handlers.onDelta).toHaveBeenCalledWith({ type: "think", delta: "想" });
    expect(handlers.onTool).toHaveBeenCalledWith({ name: "search_knowledge", status: "start" });
    expect(handlers.onHint).toHaveBeenCalledWith({ code: "EXCEED_MAX_ITERS", text: "达到上限" });
    expect(handlers.onFinish).toHaveBeenCalledWith({ messageId: "m1" });
    expect(handlers.onCancel).toHaveBeenCalledWith({ messageStatus: "INTERRUPTED" });
    expect(handlers.onError).toHaveBeenCalledWith("boom");
    expect(handlers.onDone).toHaveBeenCalledTimes(1);
    // 原始帧全量进抽屉
    expect(handlers.onEvent).toHaveBeenCalledTimes(8);
  });

  it("非事件流（业务拒绝 JSON Result）→ 抛 ApiError 带 message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        body: {}, // content-type 分支在读流前短路 不会真正消费
        json: async () => ({ code: "A0002", message: "当前会话处理中，请稍后再发起新的对话" }),
      })),
    );
    await expect(
      connectAgentSSE({ question: "问", signal: new AbortController().signal, ...baseHandlers() }),
    ).rejects.toMatchObject({ message: "当前会话处理中，请稍后再发起新的对话" });
  });

  it("HTTP 401 → 抛登录过期（redirectToLogin 由 shared 层承接）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 401, headers: new Headers() })),
    );
    await expect(
      connectAgentSSE({ question: "问", signal: new AbortController().signal, ...baseHandlers() }),
    ).rejects.toMatchObject({ message: "登录已过期，请重新登录" });
  });

  it("非 2xx → 抛流式连接失败", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 502, headers: new Headers() })),
    );
    await expect(
      connectAgentSSE({ question: "问", signal: new AbortController().signal, ...baseHandlers() }),
    ).rejects.toMatchObject({ message: "流式连接失败 (HTTP 502)" });
  });
});
