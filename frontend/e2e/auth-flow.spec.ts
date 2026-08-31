// M5 T1 E2E：认证流程（登录成功 / 密码错误 / 越权拦截）
import { expect, test } from "@playwright/test";

import { installApiMocks } from "./mock-api";

test.describe("认证流程", () => {
  test("登录成功跳转对话页", async ({ page }) => {
    await installApiMocks(page, { role: "admin" });
    await page.goto("/login");

    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("pass-123");
    await page.getByRole("button", { name: /登\s*录/ }).click();

    await expect(page).toHaveURL(/\/chat/);
    await expect(page.getByText(/开始你的第一次提问/)).toBeVisible();
  });

  test("密码错误展示错误提示", async ({ page }) => {
    await installApiMocks(page, { role: "admin", failLogin: true });
    await page.goto("/login");

    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("wrong-pass");
    await page.getByRole("button", { name: /登\s*录/ }).click();

    await expect(page.getByText("用户名或密码错误")).toBeVisible();
  });

  test("非 admin 访问管理页被重定向（越权拦截）", async ({ page }) => {
    await installApiMocks(page, { role: "user" });
    await page.goto("/login");
    await page.getByLabel("用户名").fill("alice");
    await page.getByLabel("密码").fill("pass-123");
    await page.getByRole("button", { name: /登\s*录/ }).click();
    await expect(page).toHaveURL(/\/chat/);

    await page.goto("/admin/users");
    // 非 admin 被 RequireAdmin 重定向回首页（/chat）
    await expect(page).toHaveURL(/\/chat/);
    await expect(page.getByText(/开始你的第一次提问/)).toBeVisible();
  });
});
