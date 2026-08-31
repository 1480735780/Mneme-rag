// M5 T1 E2E 后端 mock：拦截 /api/** 返回统一 Result envelope（code=0）
// 覆盖登录/me/会话/SSE/停止/知识库/用户/图谱等主链路端点
import type { Page, Route } from "@playwright/test";

export function ok(data: unknown): string {
  return JSON.stringify({ code: "0", message: "", data, requestId: "e2e-req" });
}

export function bizError(message: string, code = "A10001"): string {
  return JSON.stringify({ code, message, data: null, requestId: "e2e-req" });
}

/** SSE 帧拼接：event + data */
function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function sseChunks(question: string): string[] {
  return [
    sse("meta", { conversationId: "conv-1", taskId: "task-1" }),
    sse("message", { type: "think", delta: "正在检索知识库…" }),
    sse("message", { type: "response", delta: "这是对" }),
    sse("message", { type: "response", delta: `「${question}」的回答。` }),
    sse("finish", {
      messageId: "msg-1",
      title: "新会话",
      sources: [{ docId: "doc-1", docName: "示例文档", score: 0.95 }],
      messageStatus: "NORMAL",
    }),
    sse("done", {}),
  ];
}

/** 说明：route.fulfill({ response }) 流式体在沙箱 Edge 下不投递，改用整段 body；holdSse 去掉 done 帧使 isStreaming 保持 true */

export interface MockOptions {
  role?: "admin" | "user";
  /** 登录密码错误时返回业务错误 */
  failLogin?: boolean;
  /** SSE 流发完 meta+delta 后保持连接（测试停止） */
  holdSse?: boolean;
}

/** 注册主链路 API mock；返回 { stopRequests } 供停止用例断言 */
export async function installApiMocks(page: Page, opts: MockOptions = {}) {
  const role = opts.role ?? "admin";
  const stopRequests: string[] = [];
  const sseRequests: string[] = [];

  // 仅拦截真实 API 请求：路径以 /api/ 开头（避免 glob 误匹配 /src/shared/api/*.ts 源码模块）
  await page.route((url) => url.pathname.startsWith("/api/"), async (route: Route) => {
    const req = route.request();
    const method = req.method();
    const url = new URL(req.url());
    const path = url.pathname;
    const isSse = path.endsWith("/rag/v3/chat") && method === "GET";

    // 登录
    if (path.endsWith("/auth/login") && method === "POST") {
      const body = (req.postDataJSON() ?? {}) as { username?: string; password?: string };
      if (opts.failLogin || body.password !== "pass-123") {
        return route.fulfill({ status: 200, contentType: "application/json", body: bizError("用户名或密码错误") });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: ok({ userId: "u1", role, token: "e2e-token", avatar: "" }) });
    }
    if (path.endsWith("/auth/logout") && method === "POST") {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok(null) });
    }
    // 当前用户
    if (path.endsWith("/user/me")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok({ userId: "u1", username: role === "admin" ? "admin" : "alice", role, avatar: "" }) });
    }
    // 会话
    if (path.endsWith("/conversations") && method === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok([]) });
    }
    if (path.includes("/messages") && method === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok([]) });
    }
    // SSE 聊天
    if (isSse) {
      sseRequests.push(url.searchParams.get("question") ?? "");
      const chunks = sseChunks(url.searchParams.get("question") ?? "");
      // holdSse：去掉 done 帧 → SSE 读完但 onDone 不触发 → isStreaming 保持 true（供「停止」用例）
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: (opts.holdSse ? chunks.slice(0, -1) : chunks).join("") });
    }
    // 停止生成
    if (path.endsWith("/rag/v3/stop") && method === "POST") {
      stopRequests.push(url.searchParams.get("task_id") ?? "");
      return route.fulfill({ status: 200, contentType: "application/json", body: ok(null) });
    }
    // 知识库分页
    if (path.endsWith("/knowledge-base") && method === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok({ records: [], total: 0, size: 10, current: 1, pages: 0 }) });
    }
    // 用户分页
    if (path.endsWith("/users") && method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: ok({
          records: [
            { id: "u1", username: "admin", role: "admin", avatar: "", createTime: "2026-08-25T10:00:00Z" },
            { id: "u2", username: "alice", role: "user", avatar: "", createTime: "2026-08-25T10:00:00Z" },
          ],
          total: 2,
          current: 1,
          size: 10,
          hasMore: false,
        }),
      });
    }
    // 图谱标签 / 子图
    if (path.endsWith("/admin/kg/labels")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok(["订单", "商品"]) });
    }
    if (path.endsWith("/admin/kg/graph")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: ok({
          nodes: [
            { id: "a", name: "订单", type: "entity", description: "" },
            { id: "b", name: "商品", type: "entity", description: "" },
          ],
          edges: [{ id: "e1", source: "a", target: "b", label: "包含", description: "" }],
          truncated: false,
        }),
      });
    }
    // 其余端点兜底：成功空数据
    return route.fulfill({ status: 200, contentType: "application/json", body: ok(null) });
  });

  return { stopRequests, sseRequests };
}
