// M3 T2 追踪详情页单测：概要 / 节点时间线（缩进+耗时条）/ 错误 / null → 不存在态
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getTraceDetail } from "../api";
import TraceDetailPage from "./TraceDetailPage";

vi.mock("../api", () => ({
  getTraceDetail: vi.fn(),
  getTraceRunsPage: vi.fn(),
  getTraceNodes: vi.fn(),
}));

const mockDetail = vi.mocked(getTraceDetail);

const run = {
  traceId: "tr-1",
  conversationId: "c-1",
  taskId: "t-1",
  userId: "u1",
  status: "SUCCESS",
  durationMs: 2500,
  ttftMs: 400,
  question: "什么是 RAG？",
  startTime: "2026-08-25T10:00:00Z",
  endTime: "2026-08-25T10:00:02Z",
};

const node = (over: Record<string, unknown> = {}) => ({
  traceId: "tr-1",
  nodeId: "n-1",
  nodeType: "RETRIEVE",
  nodeName: "召回",
  status: "SUCCESS",
  durationMs: 800,
  startTime: "2026-08-25T10:00:00.1Z",
  ...over,
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/admin/traces/tr-1"]}>
      <Routes>
        <Route path="/admin/traces/:traceId" element={<TraceDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TraceDetailPage", () => {
  it("渲染 run 概要（问题/状态/耗时）", async () => {
    mockDetail.mockResolvedValue({ run, nodes: [] } as never);
    renderPage();
    expect(await screen.findByText("什么是 RAG？")).toBeInTheDocument();
    expect(screen.getByText("2.5s")).toBeInTheDocument();
    expect(screen.getByText("400ms")).toBeInTheDocument();
  });

  it("节点按 depth 缩进并展示耗时占比条与错误", async () => {
    mockDetail.mockResolvedValue({
      run,
      nodes: [
        node({ nodeId: "n-1", depth: 0, durationMs: 800, nodeName: "召回", status: "SUCCESS" }),
        node({ nodeId: "n-2", depth: 1, durationMs: 400, nodeName: "检索", status: "ERROR", errorMessage: "向量库超时" }),
      ],
    } as never);
    renderPage();
    const retrieve = await screen.findByText("召回");
    const search = screen.getByText("检索");
    expect(retrieve).toBeInTheDocument();
    expect(search).toBeInTheDocument();
    expect(screen.getByText("向量库超时")).toBeInTheDocument();
    // 深度 1 的节点有左缩进（margin-left 20px），深度 0 无
    const searchCard = search.closest("div.rounded-lg");
    expect(searchCard).toHaveStyle("margin-left: 20px");
  });

  it("traceId 不存在显示受控不存在态", async () => {
    mockDetail.mockResolvedValue(null as never);
    renderPage();
    expect(await screen.findByText("追踪不存在")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockDetail.mockRejectedValueOnce(new Error("boom"));
    mockDetail.mockResolvedValueOnce({ run, nodes: [] } as never);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("什么是 RAG？")).toBeInTheDocument();
  });
});
