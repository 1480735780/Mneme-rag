// M5 T1 E2E 运行脚本（沙箱环境用：Playwright 直连系统 Edge，绕过 test runner 的浏览器下载与挂起问题）
// 场景：登录成功 / 密码错误 / 越权拦截 / 提问渲染回答与来源 / 停止 / 管理页（知识库/用户/图谱）
// 前置：vite dev 已在 5174 启动（npm run dev -- --port 5174 --strictPort --host 127.0.0.1）
import { chromium } from "@playwright/test";

const BASE = "http://127.0.0.1:5174";

function ok(data) {
  return JSON.stringify({ code: "0", message: "", data, requestId: "e2e-req" });
}
function bizError(message) {
  return JSON.stringify({ code: "A10001", message, data: null, requestId: "e2e-req" });
}
function sse(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}
function sseChunks(question) {
  return [
    sse("meta", { conversationId: "conv-1", taskId: "task-1" }),
    sse("message", { type: "think", delta: "正在检索知识库…" }),
    sse("message", { type: "response", delta: "这是对" }),
    sse("message", { type: "response", delta: `「${question}」的回答。` }),
    sse("finish", { messageId: "msg-1", title: "新会话", sources: [{ docId: "doc-1", docName: "示例文档", score: 0.95 }], messageStatus: "NORMAL" }),
    sse("done", {}),
  ];
}
function streamingResponse(chunks, hold = false) {
  const encoder = new TextEncoder();
  let sent = 0;
  const body = new ReadableStream({
    start(controller) {
      const push = () => {
        if (sent < chunks.length) {
          controller.enqueue(encoder.encode(chunks[sent++]));
          setTimeout(push, 30);
        } else if (!hold) {
          controller.close();
        }
      };
      push();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

/** 注册 API mock（仅拦截路径以 /api/ 开头的请求，避免误伤 /src/shared/api/* 源码模块） */
async function installMocks(page, { role = "admin", failLogin = false, holdSse = false } = {}) {
  const stopRequests = [];
  const sseRequests = [];
  const uploadRequests = [];
  let uploadedDocs = []; // 上传后置 1 条，供「上传文档」场景断言列表刷新
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const req = route.request();
    const method = req.method();
    const path = new URL(req.url()).pathname;
    if (path.endsWith("/auth/login") && method === "POST") {
      const body = req.postDataJSON() ?? {};
      if (failLogin || body.password !== "pass-123") {
        return route.fulfill({ status: 200, contentType: "application/json", body: bizError("用户名或密码错误") });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: ok({ userId: "u1", role, token: "e2e-token", avatar: "" }) });
    }
    if (path.endsWith("/auth/logout") && method === "POST") return route.fulfill({ status: 200, contentType: "application/json", body: ok(null) });
    if (path.endsWith("/user/me")) return route.fulfill({ status: 200, contentType: "application/json", body: ok({ userId: "u1", username: role === "admin" ? "admin" : "alice", role, avatar: "" }) });
    if (path.endsWith("/conversations") && method === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: ok([]) });
    if (path.includes("/messages") && method === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: ok([]) });
    if (path.endsWith("/rag/v3/chat") && method === "GET") {
      const q = new URL(req.url()).searchParams.get("question") ?? "";
      sseRequests.push(q);
      const chunks = sseChunks(q);
      // holdSse：去掉 done 帧，SSE 读完但 onDone 不触发 → isStreaming 保持 true 供停止用例
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: (holdSse ? chunks.slice(0, -1) : chunks).join("") });
    }
    if (path.endsWith("/rag/v3/stop") && method === "POST") {
      stopRequests.push(new URL(req.url()).searchParams.get("task_id") ?? "");
      return route.fulfill({ status: 200, contentType: "application/json", body: ok(null) });
    }
    if (path.endsWith("/knowledge-base") && method === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok({ records: [{ id: "kb-1", name: "产品库", collectionName: "prod", documentCount: uploadedDocs.length }], total: 1, current: 1, size: 10, pages: 1 }) });
    }
    // 库内文档列表（上传后返回 1 条）
    if (/\/knowledge-base\/[^/]+\/docs$/.test(path) && method === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: ok({ records: uploadedDocs, total: uploadedDocs.length, current: 1, size: 10, pages: uploadedDocs.length ? 1 : 0 }) });
    }
    // 上传文档（multipart）
    if (/\/knowledge-base\/[^/]+\/docs\/upload$/.test(path) && method === "POST") {
      const filename = (req.postData() ?? "").match(/filename="([^"]+)"/)?.[1] ?? "file.md";
      uploadRequests.push(filename);
      uploadedDocs = [{ id: "doc-u1", kbId: "kb-1", docName: filename, sourceType: "file", status: "pending", chunkCount: 0, createTime: "2026-08-25T10:00:00Z" }];
      return route.fulfill({ status: 200, contentType: "application/json", body: ok(uploadedDocs[0]) });
    }
    if (path.endsWith("/users") && method === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: ok({ records: [{ id: "u1", username: "admin", role: "admin", avatar: "", createTime: "2026-08-25T10:00:00Z" }, { id: "u2", username: "alice", role: "user", avatar: "", createTime: "2026-08-25T10:00:00Z" }], total: 2, current: 1, size: 10, hasMore: false }) });
    if (path.endsWith("/admin/kg/labels")) return route.fulfill({ status: 200, contentType: "application/json", body: ok(["订单", "商品"]) });
    if (path.endsWith("/admin/kg/graph")) return route.fulfill({ status: 200, contentType: "application/json", body: ok({ nodes: [{ id: "a", name: "订单", type: "entity", description: "" }, { id: "b", name: "商品", type: "entity", description: "" }], edges: [{ id: "e1", source: "a", target: "b", label: "包含", description: "" }], truncated: false }) });
    return route.fulfill({ status: 200, contentType: "application/json", body: ok(null) });
  });
  return { stopRequests, sseRequests, uploadRequests };
}

const results = [];
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => results.push({ name, pass: true }))
    .catch((e) => results.push({ name, pass: false, error: e.message?.slice(0, 200) }));
}

async function login(page, username, password) {
  await page.goto(`${BASE}/login`, { timeout: 15000 });
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: /登\s*录/ }).click();
}

const browser = await chromium.launch({ channel: "msedge", headless: true });

await check("登录成功跳转对话页", async () => {
  const page = await browser.newPage();
  await installMocks(page, { role: "admin" });
  await login(page, "admin", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  await page.getByText(/开始你的第一次提问/).waitFor({ timeout: 10000 });
  await page.close();
});

await check("密码错误展示错误提示", async () => {
  const page = await browser.newPage();
  await installMocks(page, { role: "admin" });
  await login(page, "admin", "wrong");
  await page.getByText("用户名或密码错误").waitFor({ timeout: 10000 });
  await page.close();
});

await check("非 admin 访问管理页被重定向（越权）", async () => {
  const page = await browser.newPage();
  await installMocks(page, { role: "user" });
  await login(page, "alice", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  await page.goto(`${BASE}/admin/users`);
  await page.waitForURL(/\/chat/, { timeout: 10000 });
  await page.close();
});

await check("提问后流式渲染回答与来源", async () => {
  const page = await browser.newPage();
  const { sseRequests } = await installMocks(page, { role: "admin" });
  await login(page, "admin", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  await page.getByPlaceholder(/输入问题/).fill("什么是 RAG？");
  await page.getByRole("button", { name: /发\s*送/ }).click();
  await page.getByText(/这是对「什么是 RAG？」的回答/).waitFor({ timeout: 15000 });
  await page.getByText("示例文档").waitFor({ timeout: 5000 });
  if (!sseRequests.includes("什么是 RAG？")) throw new Error("SSE 请求未携带问题");
  await page.close();
});

await check("停止生成调用后端 stop 接口", async () => {
  const page = await browser.newPage();
  const { stopRequests } = await installMocks(page, { role: "admin", holdSse: true });
  await login(page, "admin", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  await page.getByPlaceholder(/输入问题/).fill("测试停止");
  await page.getByRole("button", { name: /发\s*送/ }).click();
  const stop = page.getByRole("button", { name: "停止生成" });
  await stop.waitFor({ timeout: 10000 });
  await stop.click();
  if (stopRequests.length === 0) throw new Error("未调用后端 stop 接口");
  await page.close();
});

await check("管理页：知识库列表渲染产品库", async () => {
  const page = await browser.newPage();
  await installMocks(page, { role: "admin" });
  await login(page, "admin", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  await page.goto(`${BASE}/admin/knowledge`);
  await page.getByText("产品库").waitFor({ timeout: 10000 });
  await page.close();
});

await check("上传文档后文档列表出现并刷新", async () => {
  const page = await browser.newPage();
  const { uploadRequests } = await installMocks(page, { role: "admin" });
  await login(page, "admin", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  // 进入知识库文档页
  await page.goto(`${BASE}/admin/knowledge`);
  await page.getByText("产品库").waitFor({ timeout: 10000 });
  await page.getByText("产品库").click();
  await page.waitForURL(/\/documents/, { timeout: 10000 });
  // 上传文档（multipart 走 mock）
  await page.getByRole("button", { name: /上传文档/ }).click();
  await page.setInputFiles('input[type="file"]', {
    name: "usage.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# 产品使用说明\n\n1. 注册账号\n2. 上传文档"),
  });
  await page.getByRole("button", { name: "上传", exact: true }).click();
  // 上传后列表刷新出现文档行
  await page.getByText("usage.md").waitFor({ timeout: 10000 });
  if (uploadRequests.length === 0) throw new Error("未发起上传请求");
  await page.close();
});

await check("管理页：用户管理渲染用户行", async () => {
  const page = await browser.newPage();
  await installMocks(page, { role: "admin" });
  await login(page, "admin", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  await page.goto(`${BASE}/admin/users`);
  await page.getByText("admin").first().waitFor({ timeout: 10000 });
  await page.getByText("alice").first().waitFor({ timeout: 5000 });
  await page.close();
});

await check("管理页：知识图谱渲染子图", async () => {
  const page = await browser.newPage();
  await installMocks(page, { role: "admin" });
  await login(page, "admin", "pass-123");
  await page.waitForURL(/\/chat/, { timeout: 15000 });
  await page.goto(`${BASE}/admin/graph`);
  await page.getByText("2 个节点 · 1 条边").waitFor({ timeout: 10000 });
  await page.close();
});

await browser.close();

let failed = 0;
for (const r of results) {
  console.log(`${r.pass ? "PASS" : "FAIL"}  ${r.name}${r.error ? `  →  ${r.error}` : ""}`);
  if (!r.pass) failed += 1;
}
console.log(`\nE2E: ${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
