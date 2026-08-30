// v1.1 P2 Agent SSE 传输层：GET /agent/v1/chat 七类帧（meta/message/tool/hint/finish/done/cancel）
// + error 帧；复用 workflow connectChatSSE 的连接与分帧模式（fetch + ReadableStream，
// 原生 EventSource 不支持自定义 Authorization 头）。与 workflow 协议两套分立。
import { getToken } from "@/shared/auth/storage";
import { ApiError } from "@/shared/types/api";
import { redirectToLogin } from "@/shared/api/client";

import type {
  AgentCompletionPayload,
  AgentHintPayload,
  AgentMessageDelta,
  AgentMetaPayload,
  AgentToolProgress,
} from "./types";

export interface AgentSSEHandlers {
  onMeta(payload: AgentMetaPayload): void;
  onDelta(payload: AgentMessageDelta): void;
  onTool(payload: AgentToolProgress): void;
  onHint(payload: AgentHintPayload): void;
  onFinish(payload: AgentCompletionPayload): void;
  onCancel(payload: AgentCompletionPayload): void;
  onError(message: string): void;
  onDone(): void;
  /** 每一条原始帧（原始帧抽屉消费） */
  onEvent?(event: string, data: unknown): void;
}

export interface ConnectAgentSSEOptions extends AgentSSEHandlers {
  question: string;
  conversationId?: string;
  signal: AbortSignal;
}

/** 建立 GET /agent/v1/chat 的 SSE 连接并逐帧分发；流结束或 abort 时 resolve */
export async function connectAgentSSE(options: ConnectAgentSSEOptions): Promise<void> {
  const baseURL = import.meta.env.VITE_API_BASE_URL ?? "/api";
  const params = new URLSearchParams({ question: options.question });
  if (options.conversationId) {
    // snake_case query 参数（对齐后端 conversation_id，与 workflow /rag/v3/chat 同口径）
    params.set("conversation_id", options.conversationId);
  }

  const headers: Record<string, string> = { Accept: "text/event-stream" };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${baseURL}/agent/v1/chat?${params.toString()}`, {
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

  // 业务拒绝（幂等/闸门繁忙/参数校验）返回 200 + JSON Result 而非事件流 → 取 message 抛业务错
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    const body = (await response.json().catch(() => null)) as { message?: string } | null;
    throw new ApiError(body?.message || "请求失败", { status: response.status });
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

function handleFrame(frame: string, handlers: AgentSSEHandlers): void {
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
  let parsed: unknown = data;
  try {
    parsed = JSON.parse(data);
  } catch {
    // 非 JSON 载荷按原文分发（如 done 的 [DONE]）
  }
  handlers.onEvent?.(event, parsed);
  dispatchEvent(event, parsed, handlers);
}

function dispatchEvent(event: string, payload: unknown, handlers: AgentSSEHandlers): void {
  try {
    switch (event) {
      case "meta":
        handlers.onMeta(payload as AgentMetaPayload);
        break;
      case "message":
        handlers.onDelta(payload as AgentMessageDelta);
        break;
      case "tool":
        handlers.onTool(payload as AgentToolProgress);
        break;
      case "hint":
        handlers.onHint(payload as AgentHintPayload);
        break;
      case "finish":
        handlers.onFinish(payload as AgentCompletionPayload);
        break;
      case "cancel":
        handlers.onCancel(payload as AgentCompletionPayload);
        break;
      case "error":
        handlers.onError(
          typeof payload === "object" && payload !== null && "error" in payload
            ? String((payload as { error?: unknown }).error)
            : String(payload),
        );
        break;
      case "done":
        handlers.onDone();
        break;
      default:
        break;
    }
  } catch {
    // 单帧分发失败不影响后续帧
  }
}
