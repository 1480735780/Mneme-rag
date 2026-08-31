// M2 D11/D12 预览页单测：Markdown 渲染 / 空态 / 错误态
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { downloadDocumentFile, getDocument, previewDocument } from "../api";
import KnowledgeDocumentPreviewPage from "./KnowledgeDocumentPreviewPage";

vi.mock("../api", () => ({
  getDocument: vi.fn(),
  previewDocument: vi.fn(),
  downloadDocumentFile: vi.fn(),
}));

const mockDoc = vi.mocked(getDocument);
const mockPreview = vi.mocked(previewDocument);
const mockDownload = vi.mocked(downloadDocumentFile);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/knowledge/kb-1/documents/doc-1/preview"]}>
      <Routes>
        <Route path="/admin/knowledge/:kbId/documents/:docId/preview" element={<KnowledgeDocumentPreviewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockDoc.mockResolvedValue({ id: "doc-1", kbId: "kb-1", docName: "说明.md" } as never);
});

describe("KnowledgeDocumentPreviewPage", () => {
  it("渲染 Markdown 内容（标题转成 h1）", async () => {
    mockPreview.mockResolvedValue("# 标题\n\n正文 **加粗**");
    const { container } = renderPage();
    await vi.waitFor(() => {
      const h1 = container.querySelector(".md-body h1");
      expect(h1?.textContent).toBe("标题");
    });
    expect(screen.getByText("正文")).toBeInTheDocument();
  });

  it("空内容显示空态", async () => {
    mockPreview.mockResolvedValue("   ");
    renderPage();
    expect(await screen.findByText("暂无内容")).toBeInTheDocument();
  });

  it("失败显示错误态", async () => {
    mockPreview.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("点击下载调用 blob 下载", async () => {
    mockPreview.mockResolvedValue("# 标题");
    mockDownload.mockResolvedValue(new Blob(["# 标题"], { type: "text/markdown" }));
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    renderPage();
    await screen.findByRole("heading", { level: 1 });
    await userEvent.click(screen.getByRole("button", { name: "下载源文件" }));
    await vi.waitFor(() => expect(mockDownload).toHaveBeenCalledWith("doc-1"));
    revoke.mockRestore();
  });
});
