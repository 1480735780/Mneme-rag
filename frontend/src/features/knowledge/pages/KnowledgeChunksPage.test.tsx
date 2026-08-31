// M2 C Chunk 列表页单测：三态 / CRUD / 单条启停 / 批量启停二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { batchToggleChunks, createChunk, deleteChunk, getChunksPage, getDocument, toggleChunk, updateChunk } from "../api";
import KnowledgeChunksPage from "./KnowledgeChunksPage";

vi.mock("../api", () => ({
  getDocument: vi.fn(),
  getChunksPage: vi.fn(),
  createChunk: vi.fn(),
  updateChunk: vi.fn(),
  deleteChunk: vi.fn(),
  toggleChunk: vi.fn(),
  batchToggleChunks: vi.fn(),
}));

const mockDoc = vi.mocked(getDocument);
const mockPage = vi.mocked(getChunksPage);
const mockCreate = vi.mocked(createChunk);
const mockUpdate = vi.mocked(updateChunk);
const mockDelete = vi.mocked(deleteChunk);
const mockToggle = vi.mocked(toggleChunk);
const mockBatch = vi.mocked(batchToggleChunks);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/knowledge/kb-1/documents/doc-1/chunks"]}>
      <Routes>
        <Route path="/admin/knowledge/:kbId/documents/:docId/chunks" element={<KnowledgeChunksPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const chunk = (over: Record<string, unknown> = {}) => ({
  id: "c1",
  docId: "doc-1",
  chunkIndex: 1,
  content: "第一段内容",
  contentHash: "abc123def",
  charCount: 5,
  tokenCount: 3,
  enabled: 1,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockDoc.mockResolvedValue({ id: "doc-1", kbId: "kb-1", docName: "说明.md" } as never);
});

describe("KnowledgeChunksPage", () => {
  it("加载后渲染分块行与状态", async () => {
    mockPage.mockResolvedValue({ records: [chunk()], total: 1, size: 10, current: 1, pages: 1 } as never);
    renderPage();
    expect(await screen.findByText("说明.md")).toBeInTheDocument();
    expect(screen.getByText("第一段内容")).toBeInTheDocument();
    expect(screen.getByText("启用")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    renderPage();
    expect(await screen.findByText("暂无分块")).toBeInTheDocument();
  });

  it("单条启停调用 toggle 接口", async () => {
    mockPage.mockResolvedValue({ records: [chunk()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockToggle.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("第一段内容");
    await userEvent.click(screen.getByRole("button", { name: "禁用" }));
    await waitFor(() => expect(mockToggle).toHaveBeenCalledWith("doc-1", "c1", false));
  });

  it("新增 Chunk 提交 content 与 index", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    mockCreate.mockResolvedValue({ id: "c9" } as never);
    renderPage();
    await screen.findByText("暂无分块");
    await userEvent.click(screen.getByRole("button", { name: "新增 Chunk" }));
    await userEvent.type(screen.getByLabelText("序号（可选）"), "99");
    await userEvent.type(screen.getByLabelText("内容"), "手写内容");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith("doc-1", { content: "手写内容", index: 99 }),
    );
  });

  it("编辑 Chunk 调用 update 接口", async () => {
    mockPage.mockResolvedValue({ records: [chunk()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockUpdate.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("第一段内容");
    await userEvent.click(screen.getByRole("button", { name: "编辑" }));
    const ta = await screen.findByLabelText("内容");
    await userEvent.clear(ta);
    await userEvent.type(ta, "修改后的内容");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("doc-1", "c1", { content: "修改后的内容" }));
  });

  it("勾选后批量启停需二次确认", async () => {
    mockPage.mockResolvedValue({
      records: [chunk({ id: "c1" }), chunk({ id: "c2", chunkIndex: 2, content: "第二段" })],
      total: 2,
      size: 10,
      current: 1,
      pages: 1,
    } as never);
    mockBatch.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("第一段内容");
    await userEvent.click(screen.getByRole("checkbox", { name: "选择分块 1" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择分块 2" }));
    await userEvent.click(screen.getByRole("button", { name: "启用所选" }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(mockBatch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "启用" }));
    await waitFor(() => expect(mockBatch).toHaveBeenCalledWith("doc-1", true, ["c1", "c2"]));
  });

  it("删除 Chunk 需二次确认", async () => {
    mockPage.mockResolvedValue({ records: [chunk()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockDelete.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("第一段内容");
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(await screen.findByText("删除 Chunk")).toBeInTheDocument();
    expect(mockDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("doc-1", "c1"));
  });
});
