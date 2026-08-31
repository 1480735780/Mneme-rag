// M5 T1 E2E：聊天主链路（提问 → 流式回答 → 来源 → 停止）
import { expect, test } from "@playwright/test";

import { installApiMocks } from "./mock-api";

test.describe("聊天主链路", () => {
  test("提问后流式渲染回答与来源", async ({ page }) => {
    const { sseRequests } = await installApiMocks(page, { role: "admin" });
    await page.goto("/login");
    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("pass-123");
    await page.getByRole("button", { name: /登\s*录/ }).click();
    await expect(page).toHaveURL(/\/chat/);

    await page.getByPlaceholder(/输入问题/).fill("什么是 RAG？");
    await page.getByRole("button", { name: /发\s*送/ }).click();

    // 流式回答内容出现
    await expect(page.getByText(/这是对「什么是 RAG？」的回答/)).toBeVisible();
    // 来源引用渲染
    await expect(page.getByText("示例文档")).toBeVisible();
    // 确实发起了 SSE 请求
    expect(sseRequests).toContain("什么是 RAG？");
  });

  test("停止生成调用后端 stop 接口", async ({ page }) => {
    const { stopRequests } = await installApiMocks(page, { role: "admin", holdSse: true });
    await page.goto("/login");
    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("pass-123");
    await page.getByRole("button", { name: /登\s*录/ }).click();
    await expect(page).toHaveURL(/\/chat/);

    await page.getByPlaceholder(/输入问题/).fill("测试停止");
    await page.getByRole("button", { name: /发\s*送/ }).click();

    // 流式中出现停止按钮
    const stop = page.getByRole("button", { name: "停止生成" });
    await expect(stop).toBeVisible();
    await stop.click();
    await expect(stop).toBeHidden();
    // 停止请求携带 taskId
    expect(stopRequests.length).toBeGreaterThan(0);
  });
});
