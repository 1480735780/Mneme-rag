// M5 T1 Playwright E2E 配置
// - 浏览器安装到项目内 .e2e-browsers（沙箱禁止写 %LOCALAPPDATA%\ms-playwright）
// - webServer 用 vite dev（端口 5173）；后端请求全部由 e2e/mock-api.ts 的 page.route 拦截
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.join(import.meta.dirname, ".playwright-browsers");

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5174",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // 沙箱无法下载 Playwright 自带 Chromium → 复用系统已装 Edge
    { name: "msedge", use: { ...devices["Desktop Edge"], channel: "msedge" } },
  ],
  webServer: {
    command: "npm run dev -- --port 5174 --strictPort --host 127.0.0.1",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
