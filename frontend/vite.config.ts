import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// M0 #1/#2：Vite 工程脚手架 + Tailwind v4 + Vitest
// - /api 代理到后端 FastAPI（127.0.0.1:8000），rewrite 剥掉 /api 前缀
// - @ 别名指向 src（feature-first）
// - vitest：jsdom 环境 + setup 文件
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    css: true,
    // 只跑 src 单测；e2e 目录为 Playwright spec（M5 T1），由 playwright test 运行
    include: ["src/**/*.{test,spec}.?(c|m)[jt]s?(x)"],
    // 本机资源受限：单 worker 串行，避免多 worker 启动超时
    fileParallelism: false,
    maxWorkers: 1,
  },
});
