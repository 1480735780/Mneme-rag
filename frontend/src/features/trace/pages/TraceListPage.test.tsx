// M3 T1 链路追踪列表页单测：三态 / 过滤 URL 同步 / 分页 / 跳详情
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getTraceRunsPage } from "../api";
import TraceListPage from "./TraceListPage";

vi.mock("../api", () => ({
  getTraceRunsPage: vi.fn(),
  getTraceDetail: vi.fn(),
  getTraceNodes: vi.fn(),
}));

const mockPage = vi.mocked(getTraceRunsPage);

const run = (over: Record<string, unknown> = {}) => ({
  traceId: "tr-1",
  traceName: "chat",
  conversationId: "c-1",
  taskId: "t-1",
  userId: "u1",
  status: "SUCCESS",
  durationMs: 1200,
  ttftMs: 350,
  question: "什么是 RAG？",
  startTime: "2026-08-25T10:00:00Z",
  ...over,
});

function renderPage(initialPath = "/admin/traces") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/admin/traces" element={<TraceListPage />} />
        <Route path="/admin/traces/:traceId" element={<div>trace-detail</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TraceListPage", () => {
  it("加载后渲染运行行，点击跳转详情", async () => {
    mockPage.mockResolvedValue({ records: [run()], total: 1, current: 1, size: 10 } as never);
    renderPage();
    expect(await screen.findByText("什么是 RAG？")).toBeInTheDocument();
    expect(screen.getByText("tr-1")).toBeInTheDocument();
    await userEvent.click(screen.getByText("什么是 RAG？"));
    expect(await screen.findByText("trace-detail")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, current: 1, size: 10 } as never);
    renderPage();
    expect(await screen.findByText("暂无追踪记录")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockPage.mockRejectedValueOnce(new Error("boom"));
    mockPage.mockResolvedValueOnce({ records: [], total: 0, current: 1, size: 10 } as never);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无追踪记录")).toBeInTheDocument();
  });

  it("搜索写入 URL 并按 traceId 重新查询", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, current: 1, size: 10 } as never);
    renderPage();
    await screen.findByText("暂无追踪记录");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({ records: [run()], total: 1, current: 1, size: 10 } as never);
    await userEvent.type(screen.getByPlaceholderText("按 traceId 过滤"), "tr-99");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("什么是 RAG？")).toBeInTheDocument();
    expect(mockPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ traceId: "tr-99", current: 1 }),
    );
  });

  it("状态下拉过滤同步到查询参数", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, current: 1, size: 10 } as never);
    renderPage();
    await screen.findByText("暂无追踪记录");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({ records: [run({ status: "ERROR" })], total: 1, current: 1, size: 10 } as never);
    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click((await screen.findAllByText("失败"))[0]);
    expect(await screen.findByText("什么是 RAG？")).toBeInTheDocument();
    expect(mockPage).toHaveBeenLastCalledWith(expect.objectContaining({ status: "ERROR" }));
  });

  it("分页点击第 2 页带 current 重新查询", async () => {
    mockPage.mockResolvedValue({ records: Array.from({ length: 10 }, (_, i) => run({ traceId: `tr-${i}` })), total: 25, current: 1, size: 10 } as never);
    renderPage();
    await screen.findByText("共 25 条");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({ records: [run({ traceId: "tr-20" })], total: 25, current: 2, size: 10 } as never);
    await userEvent.click(screen.getByRole("button", { name: "2" }));
    await waitFor(() => expect(mockPage).toHaveBeenCalledWith(expect.objectContaining({ current: 2 })));
  });

  it("从 Chat 携带 taskId 跳转时预填过滤", async () => {
    mockPage.mockResolvedValue({ records: [run()], total: 1, current: 1, size: 10 } as never);
    renderPage("/admin/traces?taskId=t-9");
    await screen.findByText("什么是 RAG？");
    expect(mockPage).toHaveBeenCalledWith(expect.objectContaining({ taskId: "t-9" }));
    expect(screen.getByPlaceholderText("按 taskId")).toHaveValue("t-9");
  });
});
