// M2 K 列表页单测：加载三态 / 创建 / 重命名 / 删除二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createKnowledgeBase, deleteKnowledgeBase, getKnowledgeBasesPage, updateKnowledgeBase } from "../api";
import { getSystemSettings } from "@/shared/api/settings";
import KnowledgeListPage from "./KnowledgeListPage";

vi.mock("../api", () => ({
  getKnowledgeBasesPage: vi.fn(),
  createKnowledgeBase: vi.fn(),
  updateKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
}));

vi.mock("@/shared/api/settings", () => ({
  getSystemSettings: vi.fn(),
}));

const mockPage = vi.mocked(getKnowledgeBasesPage);
const mockCreate = vi.mocked(createKnowledgeBase);
const mockUpdate = vi.mocked(updateKnowledgeBase);
const mockDelete = vi.mocked(deleteKnowledgeBase);
const mockSettings = vi.mocked(getSystemSettings);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/knowledge"]}>
      <Routes>
        <Route path="/admin/knowledge" element={<KnowledgeListPage />} />
        <Route path="/admin/knowledge/:kbId/documents" element={<div>documents-page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSettings.mockResolvedValue({ ai: { embedding: { defaultModel: "m1", candidates: [{ id: "m1", model: "embed-1" }] } } });
});

describe("KnowledgeListPage", () => {
  it("加载后渲染知识库行，点击跳转文档页", async () => {
    mockPage.mockResolvedValue({
      records: [{ id: "kb-1", name: "产品库", collectionName: "prod", documentCount: 3, updateTime: "2026-08-01T10:00:00" }],
      total: 1,
      size: 10,
      current: 1,
      pages: 1,
    });
    renderPage();
    expect(await screen.findByText("产品库")).toBeInTheDocument();
    expect(screen.getByText("prod")).toBeInTheDocument();
    await userEvent.click(screen.getByText("产品库"));
    expect(await screen.findByText("documents-page")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 });
    renderPage();
    expect(await screen.findByText("暂无知识库")).toBeInTheDocument();
  });

  it("加载失败显示错误态并可重试", async () => {
    mockPage.mockRejectedValueOnce(new Error("boom"));
    mockPage.mockResolvedValueOnce({ records: [], total: 0, size: 10, current: 1, pages: 0 });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无知识库")).toBeInTheDocument();
  });

  it("搜索关键词后带 name 重新分页", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 });
    renderPage();
    await screen.findByText("暂无知识库");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({
      records: [{ id: "kb-2", name: "命中库", collectionName: "hit", documentCount: 0 }],
      total: 1,
      size: 10,
      current: 1,
      pages: 1,
    });
    await userEvent.type(screen.getByPlaceholderText("搜索名称"), "命中");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("命中库")).toBeInTheDocument();
    expect(mockPage).toHaveBeenLastCalledWith(1, 10, "命中");
  });

  it("创建知识库：打开弹窗提交并刷新列表", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 });
    mockCreate.mockResolvedValue("kb-new");
    renderPage();
    await screen.findByText("暂无知识库");
    await userEvent.click(screen.getByRole("button", { name: "新建知识库" }));
    await userEvent.type(screen.getByLabelText("名称"), "新库");
    await userEvent.type(screen.getByLabelText("向量集合名"), "new_col");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({ name: "新库", collectionName: "new_col", embeddingModel: "m1" }));
  });

  it("创建知识库：系统无默认嵌入模型时提交 null", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 });
    mockCreate.mockResolvedValue("kb-new");
    mockSettings.mockResolvedValue({ ai: { embedding: { candidates: [] } } });
    renderPage();
    await screen.findByText("暂无知识库");
    await userEvent.click(screen.getByRole("button", { name: "新建知识库" }));
    await userEvent.type(screen.getByLabelText("名称"), "新库");
    await userEvent.type(screen.getByLabelText("向量集合名"), "new_col");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({ name: "新库", collectionName: "new_col", embeddingModel: null }));
  });

  it("重命名知识库后刷新列表", async () => {
    mockPage.mockResolvedValue({
      records: [{ id: "kb-1", name: "旧名", collectionName: "c1", documentCount: 0 }],
      total: 1,
      size: 10,
      current: 1,
      pages: 1,
    });
    mockUpdate.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("旧名");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("重命名"));
    const input = screen.getByLabelText("名称");
    await userEvent.clear(input);
    await userEvent.type(input, "新名");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("kb-1", { name: "新名" }));
  });

  it("删除知识库需二次确认后才调用 API", async () => {
    mockPage.mockResolvedValue({
      records: [{ id: "kb-1", name: "待删库", collectionName: "c1", documentCount: 0 }],
      total: 1,
      size: 10,
      current: 1,
      pages: 1,
    });
    mockDelete.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("待删库");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("删除"));
    expect(await screen.findByText("删除知识库")).toBeInTheDocument();
    expect(mockDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("kb-1"));
  });
});
