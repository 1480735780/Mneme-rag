// M1 #3 SSE 流式连接：fetch + ReadableStream 分帧解析（原生 EventSource 不支持自定义 Authorization 头）
// 帧格式：event: <name>\ndata: <json>\n\n
import { getToken } from "@/shared/auth/storage";
import { ApiError } from "@/shared/types/api";

import { redirectToLogin } from "@/shared/api/client";

import type { CompletionPayload, MessageDeltaPayload, StreamMetaPayload } from "./types";

export interface SSEHandlers {
  onMeta(payload: StreamMetaPayload): void;
  onDelta(payload: MessageDeltaPayload): void;
  onFinish(payload: CompletionPayload): void;
  onCancel(payload: CompletionPayload): void;
  onReject(message: string): void;
  onError(message: string): void;
  onDone(): void;
}

export interface ConnectChatSSEOptions extends SSEHandlers {
  question: string;
  conversationId?: string;
  deepThinking?: boolean;
  signal: AbortSignal;
}

/** 建立 GET /rag/v3/chat 的 SSE 连接并逐帧分发事件；流结束或 abort 时 resolve */
export async function connectChatSSE(options: ConnectChatSSEOptions): Promise<void> {
  const baseURL = import.meta.env.VITE_API_BASE_URL ?? "/api";
  const params = new URLSearchParams({ question: options.question });
  if (options.conversationId) {
    params.set("conversation_id", options.conversationId);
  }
  params.set("deep_thinking", String(Boolean(options.deepThinking)));

  const headers: Record<string, string> = { Accept: "text/event-stream" };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${baseURL}/rag/v3/chat?${params.toString()}`, {
      headers,
      signal: options.signal,
    });
  } catch {
    if (options.signal.aborted) return;
    throw new ApiError("网络错误，无法连接流式服务", { status: 0 });
  }

  if (response.status === 401) {
    redirectToLogin();
    throw new ApiError("登录已过期，请重新登录", { status: 401 });
  }
  if (!response.ok || !response.body) {
    throw new ApiError(`流式连接失败 (HTTP ${response.status})`, { status: response.status });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // 每个 SSE 帧以 \n\n 分隔
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep = buffer.indexOf("\n\n");
    while (sep !== -1) {
      handleFrame(buffer.slice(0, sep), options);
      buffer = buffer.slice(sep + 2);
      sep = buffer.indexOf("\n\n");
    }
  }
  // 尾部无 \n\n 的残留帧（后端异常关闭等情况）
  if (buffer.trim()) {
    handleFrame(buffer, options);
  }
}

function handleFrame(frame: string, handlers: SSEHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  if (dataLines.length === 0) return;
  const data = dataLines.join("\n");
  dispatchEvent(event, data, handlers);
}

function dispatchEvent(event: string, data: string, handlers: SSEHandlers): void {
  try {
    switch (event) {
      case "meta":
        handlers.onMeta(JSON.parse(data) as StreamMetaPayload);
        break;
      case "message":
        handlers.onDelta(JSON.parse(data) as MessageDeltaPayload);
        break;
      case "finish":
        handlers.onFinish(JSON.parse(data) as CompletionPayload);
        break;
      case "cancel":
        handlers.onCancel(JSON.parse(data) as CompletionPayload);
        break;
      case "reject":
        handlers.onReject(data);
        break;
      case "error":
        handlers.onError(data);
        break;
      case "done":
        handlers.onDone();
        break;
      default:
        break;
    }
  } catch {
    // 单帧解析失败不影响后续帧
  }
}
