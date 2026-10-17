// P10.3 T3.4 Dashboard 成本区页面测试：总览 KPI / 性能 / 趋势 + 成本 KPI 三张 / 按日曲线 / 模型分布 / trace 下钻
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDashboardCost, getDashboardOverview, getDashboardPerformance, getDashboardTrends } from "../api";
import DashboardPage from "./DashboardPage";

vi.mock("../api", () => ({
  getDashboardOverview: vi.fn(),
  getDashboardPerformance: vi.fn(),
  getDashboardTrends: vi.fn(),
  getDashboardCost: vi.fn(),
}));

const mockOverview = vi.mocked(getDashboardOverview);
const mockPerf = vi.mocked(getDashboardPerformance);
const mockTrends = vi.mocked(getDashboardTrends);
const mockCost = vi.mocked(getDashboardCost);

const overview = (over: Record<string, unknown> = {}) => ({
  window: "24h",
  compareWindow: "prev_24h",
  kpis: {
    totalUsers: { value: 100, delta: 10, deltaPct: 11.1 },
    activeUsers: { value: 50, delta: 5, deltaPct: 11.1 },
    totalSessions: { value: 200, delta: 20, deltaPct: null },
    sessions24h: { value: 30, delta: -3, deltaPct: -9.1 },
    totalMessages: { value: 500, delta: 50, deltaPct: 11.1 },
    messages24h: { value: 80, delta: 8, deltaPct: 11.1 },
  },
  ...over,
});

const perf = {
  window: "24h",
  avgLatencyMs: 1200,
  p95LatencyMs: 3450,
  successRate: 98.5,
  errorRate: 1.5,
  noDocRate: 3.2,
  slowRate: 0.8,
};

const trends = (series: unknown[] = []) => ({
  metric: "sessions",
  window: "24h",
  granularity: "hour",
  series,
});

const cost = (over: Record<string, unknown> = {}) => ({
  updatedAt: 1760000000000,
  businessDay: "2026-10-17",
  kpis: {
    costToday: { valueUsd: 1.5, calls: 3, unpricedCalls: 0 },
    costMonth: { valueUsd: 12.3, calls: 40, unpricedCalls: 2 },
    estimatedShare: { valuePct: 20.0, estimatedRows: 2, totalRows: 10 },
  },
  unpriced: { sharePct: 5.0, rows: 2, totalRows: 40 },
  daily: [
    { ts: 1760000000000, value: 0.2, date: "2026-10-11", calls: 2, unpriced: 0 },
    { ts: 1760086400000, value: 0.5, date: "2026-10-12", calls: 4, unpriced: 1 },
    { ts: 1760172800000, value: 0.8, date: "2026-10-13", calls: 6, unpriced: 0 },
    { ts: 1760259200000, value: 1.1, date: "2026-10-14", calls: 5, unpriced: 0 },
    { ts: 1760345600000, value: 0.9, date: "2026-10-15", calls: 8, unpriced: 1 },
    { ts: 1760432000000, value: 1.4, date: "2026-10-16", calls: 9, unpriced: 0 },
    { ts: 1760518400000, value: 1.5, date: "2026-10-17", calls: 3, unpriced: 0 },
  ],
  byModel: [
    { model: "qwen-max", costUsd: 8.8, calls: 20 },
    { model: "gpt-x", costUsd: 3.5, calls: 20 },
  ],
  topTraces: [{ traceId: "tr-1", costUsd: 0.9, calls: 2 }],
  ...over,
});

const costDetail = {
  updatedAt: 1760000000000,
  traceId: "tr-1",
  calls: [
    {
      id: "1",
      requestUid: "rq-1",
      traceId: "tr-1",
      callKind: "chat",
      model: "qwen-max",
      status: "success",
      promptTokens: 100,
      completionTokens: 50,
      tokenSource: "real",
      attemptNo: 1,
      latencyMs: 900,
      costUsd: 0.45,
      pricingSnapshot: { pricingVersion: "v1", status: "priced" },
      createTime: "2026-10-17T08:00:00",
    },
    {
      id: "2",
      requestUid: "rq-2",
      traceId: "tr-1",
      callKind: "embedding",
      model: "text-embedding-v4",
      status: "success",
      promptTokens: 80,
      completionTokens: 0,
      tokenSource: "real",
      attemptNo: 1,
      latencyMs: 120,
      costUsd: null,
      pricingSnapshot: { pricingVersion: "v1", status: "unpriced", reason: "model_unpriced" },
      createTime: "2026-10-17T08:00:01",
    },
  ],
};

function renderPage(initialPath = "/admin/dashboard") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/admin/dashboard" element={<DashboardPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // cost 默认桩（页面并行拉取成本区；下钻用例可覆写第二次调用返回明细）
  mockCost.mockResolvedValue(cost() as never);
});

describe("DashboardPage", () => {
  it("渲染六 KPI 与环比", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    renderPage();
    expect(await screen.findByText("总用户")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getAllByText("+11.1%").length).toBeGreaterThan(0);
    expect(screen.getByText("-9.1%")).toBeInTheDocument();
  });

  it("环比 null 显示占位", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    renderPage();
    await screen.findByText("总用户");
    expect(screen.getByText("200")).toBeInTheDocument();
    // totalSessions 环比为 null → 显示 "-"
    expect(screen.getAllByText("-").length).toBeGreaterThan(0);
  });

  it("渲染性能指标", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    renderPage();
    expect(await screen.findByText("平均延迟")).toBeInTheDocument();
    expect(screen.getByText("1.2s")).toBeInTheDocument();
    expect(screen.getByText("98.5%")).toBeInTheDocument();
    expect(screen.getByText("3.5s")).toBeInTheDocument();
  });

  it("空指标 series 显示空态", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends([]) as never);
    renderPage();
    expect(await screen.findByText("暂无趋势数据")).toBeInTheDocument();
  });

  it("非空 series 渲染图表", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(
      trends([{ name: "会话数", data: [{ ts: 1000, value: 1 }, { ts: 2000, value: 2 }] }]) as never,
    );
    renderPage();
    // 页面现有两张图（指标序列 + 成本按日曲线），均可渲染
    expect((await screen.findAllByTestId("trend-chart")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("会话数")).toBeInTheDocument();
  });

  it("切换时间窗 window 并重查询", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    renderPage();
    await screen.findByText("总用户");
    mockOverview.mockClear();
    mockPerf.mockClear();
    mockTrends.mockClear();
    mockOverview.mockResolvedValue(overview({ window: "7d" }) as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    await userEvent.click(screen.getByRole("combobox", { name: "时间窗口" }));
    await userEvent.click(await screen.findByText("近 7 天"));
    await vi.waitFor(() => {
      expect(mockOverview).toHaveBeenCalledWith("7d");
      expect(mockTrends).toHaveBeenCalledWith(expect.objectContaining({ window: "7d" }));
    });
  });
});

describe("DashboardPage 成本区（P10.3 T3.4）", () => {
  it("渲染成本 KPI 三张与业务日提示", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    renderPage();
    expect(await screen.findByText("当日成本")).toBeInTheDocument();
    expect(screen.getByText("$1.5000")).toBeInTheDocument();
    expect(screen.getByText("$12.3000")).toBeInTheDocument();
    expect(screen.getByText("估算占比")).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument();
    // 未定价行提示（当月 2 行）
    expect(screen.getByText("业务日 2026-10-17 · 未定价 2 行（5.0%）")).toBeInTheDocument();
    // 未触发 P2 阈值（估算占比 20% ≤ 30）
    expect(screen.queryByText("P2")).not.toBeInTheDocument();
  });

  it("估算占比超 30% 出 P2 提示", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    mockCost.mockResolvedValue(
      cost({ kpis: { costToday: { valueUsd: 1.5, calls: 3, unpricedCalls: 0 },
                       costMonth: { valueUsd: 12.3, calls: 40, unpricedCalls: 2 },
                       estimatedShare: { valuePct: 42.5, estimatedRows: 17, totalRows: 40 } } }) as never,
    );
    renderPage();
    expect(await screen.findByText("42.5%")).toBeInTheDocument();
    expect(screen.getByText("P2")).toBeInTheDocument();
  });

  it("渲染按日成本曲线与按模型分布", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    renderPage();
    expect(await screen.findByText("按日成本（近 7 个业务日）")).toBeInTheDocument();
    expect(screen.getByText("按模型（当月）")).toBeInTheDocument();
    expect(screen.getByText("qwen-max")).toBeInTheDocument();
    expect(screen.getByText("gpt-x")).toBeInTheDocument();
  });

  it("点击 Top trace 下钻到调用明细并关闭", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    // 每次 load 消耗两个 cost 调用：[常驻总览(无参), 下钻(仅 traceId 非空时)]——按队列对齐：
    // 初始 load 取 #1；点击下钻后的 load 取 #2(总览)+#3(明细)；关闭后回落默认总览
    mockCost
      .mockResolvedValueOnce(cost() as never)
      .mockResolvedValueOnce(cost() as never)
      .mockResolvedValueOnce(costDetail as never);
    renderPage();
    await screen.findByText("当日成本");
    await userEvent.click(screen.getByRole("button", { name: "tr-1" }));
    await vi.waitFor(() => {
      expect(mockCost).toHaveBeenCalledWith("tr-1");
    });
    expect(await screen.findByTestId("cost-call-table")).toBeInTheDocument();
    expect(screen.getByText("$0.4500")).toBeInTheDocument();
    // 快照解读为徽标：priced→已计价、unpriced→带 reason
    expect(screen.getByText("已计价")).toBeInTheDocument();
    expect(screen.getByText("未定价 · model_unpriced")).toBeInTheDocument();
    // 关闭下钻：detail 清空 + 再取总览
    await userEvent.click(screen.getByRole("button", { name: "关闭下钻" }));
    await vi.waitFor(() => {
      expect(mockCost).toHaveBeenLastCalledWith();
    });
    await waitFor(() => expect(screen.queryByTestId("cost-call-table")).not.toBeInTheDocument());
  });

  it("URL 直达 traceId 深链直接进入下钻", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends() as never);
    // 深链首次 load 即两个调用：[总览 → #1；traceId 明细 → #2]
    mockCost.mockResolvedValueOnce(cost() as never).mockResolvedValueOnce(costDetail as never);
    renderPage("/admin/dashboard?traceId=tr-1");
    expect(await screen.findByTestId("cost-call-table")).toBeInTheDocument();
    expect(screen.getByText("调用明细 · tr-1")).toBeInTheDocument();
  });
});
