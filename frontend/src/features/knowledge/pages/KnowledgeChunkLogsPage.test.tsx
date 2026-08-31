// M2 D10 日志页单测：三态 + 耗时展示
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getChunkLogsPage, getDocument } from "../api";
import KnowledgeChunkLogsPage from "./KnowledgeChunkLogsPage";

vi.mock("../api", () => ({
  getDocument: vi.fn(),
  getChunkLogsPage: vi.fn(),
}));

const mockDoc = vi.mocked(getDocument);
const mockLogs = vi.mocked(getChunkLogsPage);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/knowledge/kb-1/documents/doc-1/logs"]}>
      <Routes>
        <Route path="/admin/knowledge/:kbId/documents/:docId/logs" element={<KnowledgeChunkLogsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockDoc.mockResolvedValue({ id: "doc-1", kbId: "kb-1", docName: "说明.md" } as never);
});

describe("KnowledgeChunkLogsPage", () => {
  it("加载后渲染日志行（状态/耗时/错误）", async () => {
    mockLogs.mockResolvedValue({
      records: [
        {
          id: "log-1",
          docId: "doc-1",
          status: "success",
          processMode: "chunk",
          parseProfile: "fast",
          chunkCount: 3,
          totalDuration: 1200,
          extractDuration: 100,
          chunkDuration: 200,
          embedDuration: 700,
          persistDuration: 200,
          startTime: "2026-08-01T10:00:00",
          endTime: "2026-08-01T10:00:01",
        },
        {
          id: "log-2",
          docId: "doc-1",
          status: "failed",
          chunkCount: 0,
          totalDuration: 50,
          errorMessage: "解析失败",
        },
      ],
      total: 2,
      size: 10,
      current: 1,
      pages: 1,
    } as never);
    renderPage();
    expect(await screen.findByText("说明.md")).toBeInTheDocument();
    expect(screen.getAllByText("成功").length).toBeGreaterThan(0);
    expect(screen.getByText("1200ms")).toBeInTheDocument();
    expect(screen.getByText("解析失败")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockLogs.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    renderPage();
    expect(await screen.findByText("暂无分块日志")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockLogs.mockRejectedValueOnce(new Error("boom"));
    mockLogs.mockResolvedValueOnce({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
