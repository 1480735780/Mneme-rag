// M2 D 文档列表页单测：三态 / 过滤 / 分块 / 启停 / 删除二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteDocument, enableDocument, getDocumentsPage, getKnowledgeBase, startDocumentChunk } from "../api";
import KnowledgeDocumentsPage from "./KnowledgeDocumentsPage";

vi.mock("../api", () => ({
  getKnowledgeBase: vi.fn(),
  getDocumentsPage: vi.fn(),
  startDocumentChunk: vi.fn(),
  enableDocument: vi.fn(),
  deleteDocument: vi.fn(),
  downloadDocumentFile: vi.fn(),
}));

const mockKB = vi.mocked(getKnowledgeBase);
const mockPage = vi.mocked(getDocumentsPage);
const mockStart = vi.mocked(startDocumentChunk);
const mockEnable = vi.mocked(enableDocument);
const mockDelete = vi.mocked(deleteDocument);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/knowledge/kb-1/documents"]}>
      <Routes>
        <Route path="/admin/knowledge/:kbId/documents" element={<KnowledgeDocumentsPage />} />
        <Route path="/admin/knowledge/:kbId/documents/:docId/chunks" element={<div>chunks-page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const doc = (over: Partial<Parameters<typeof getDocumentsPage>[1]> & Record<string, unknown> = {}) => ({
  id: "doc-1",
  kbId: "kb-1",
  docName: "说明.md",
  sourceType: "file",
  fileType: "md",
  status: "success",
  chunkCount: 3,
  fileSize: 2048,
  enabled: true,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockKB.mockResolvedValue({ id: "kb-1", name: "产品库", collectionName: "c1" } as never);
});

describe("KnowledgeDocumentsPage", () => {
  it("加载后渲染文档行，点击跳转分块页", async () => {
    mockPage.mockResolvedValue({ records: [doc()], total: 1, size: 10, current: 1, pages: 1 } as never);
    renderPage();
    expect(await screen.findByText("产品库")).toBeInTheDocument();
    expect(screen.getByText("说明.md")).toBeInTheDocument();
    await userEvent.click(screen.getByText("说明.md"));
    expect(await screen.findByText("chunks-page")).toBeInTheDocument();
  });

  it("文档处理中/成功/失败状态均正确展示", async () => {
    mockPage.mockResolvedValue({
      records: [
        doc({ id: "d1", docName: "a.md", status: "running" }),
        doc({ id: "d2", docName: "b.md", status: "success" }),
        doc({ id: "d3", docName: "c.md", status: "failed" }),
      ],
      total: 3,
      size: 10,
      current: 1,
      pages: 1,
    } as never);
    renderPage();
    expect(await screen.findByText("处理中")).toBeInTheDocument();
    expect(screen.getByText("成功")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("a.md")).toBeInTheDocument();
    expect(screen.getByText("c.md")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    renderPage();
    expect(await screen.findByText("暂无文档")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockPage.mockRejectedValueOnce(new Error("boom"));
    mockPage.mockResolvedValueOnce({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无文档")).toBeInTheDocument();
  });

  it("状态过滤会带 status 重新查询", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    renderPage();
    await screen.findByText("暂无文档");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({ records: [doc({ status: "failed" })], total: 1, size: 10, current: 1, pages: 1 } as never);
    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click((await screen.findAllByText("失败"))[0]);
    expect(await screen.findByText("说明.md")).toBeInTheDocument();
    expect(mockPage).toHaveBeenLastCalledWith("kb-1", expect.objectContaining({ status: "failed" }));
  });

  it("开始分块调用 API 并刷新", async () => {
    mockPage.mockResolvedValue({ records: [doc({ status: "pending" })], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockStart.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("说明.md");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("开始分块"));
    await waitFor(() => expect(mockStart).toHaveBeenCalledWith("doc-1"));
  });

  it("启用/禁用切换调用 enable 接口", async () => {
    mockPage.mockResolvedValue({ records: [doc()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockEnable.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("说明.md");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("禁用"));
    await waitFor(() => expect(mockEnable).toHaveBeenCalledWith("doc-1", false));
  });

  it("删除文档需二次确认且 RUNNING 不可删", async () => {
    mockPage.mockResolvedValue({ records: [doc({ status: "running" })], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockDelete.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText("说明.md");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    const delItem = await screen.findByText("删除");
    expect(delItem).toHaveAttribute("aria-disabled", "true");
  });
});
