// M5 T1 E2E：管理页主链路（知识库 / 用户 / 图谱 导航与渲染）
import { expect, test } from "@playwright/test";

import { installApiMocks } from "./mock-api";

test.describe("管理后台主链路", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, { role: "admin" });
    await page.goto("/login");
    await page.getByLabel("用户名").fill("admin");
    await page.getByLabel("密码").fill("pass-123");
    await page.getByRole("button", { name: /登\s*录/ }).click();
    await expect(page).toHaveURL(/\/chat/);
  });

  test("知识库页渲染空态", async ({ page }) => {
    await page.goto("/admin/knowledge");
    await expect(page.getByText("知识库")).toBeVisible();
    await expect(page.getByText("暂无知识库")).toBeVisible();
  });

  test("用户管理页渲染用户行", async ({ page }) => {
    await page.goto("/admin/users");
    await expect(page.getByText("用户管理")).toBeVisible();
    await expect(page.getByText("admin")).toBeVisible();
    await expect(page.getByText("alice")).toBeVisible();
  });

  test("知识图谱页渲染子图", async ({ page }) => {
    await page.goto("/admin/graph");
    await expect(page.getByText("知识图谱")).toBeVisible();
    await expect(page.getByText("2 个节点 · 1 条边")).toBeVisible();
    await expect(page.getByText("订单")).toBeVisible();
  });
});
