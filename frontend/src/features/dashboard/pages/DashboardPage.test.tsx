// M3 Dashboard 页单测：六 KPI / 环比 null / 性能 / 趋势空态与图表 / window 过滤
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDashboardOverview, getDashboardPerformance, getDashboardTrends } from "../api";
import DashboardPage from "./DashboardPage";

vi.mock("../api", () => ({
  getDashboardOverview: vi.fn(),
  getDashboardPerformance: vi.fn(),
  getDashboardTrends: vi.fn(),
}));

const mockOverview = vi.mocked(getDashboardOverview);
const mockPerf = vi.mocked(getDashboardPerformance);
const mockTrends = vi.mocked(getDashboardTrends);

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

  it("趋势空 series 显示空态", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(trends([]) as never);
    renderPage();
    expect(await screen.findByText("暂无趋势数据")).toBeInTheDocument();
  });

  it("趋势有 series 渲染图表", async () => {
    mockOverview.mockResolvedValue(overview() as never);
    mockPerf.mockResolvedValue(perf as never);
    mockTrends.mockResolvedValue(
      trends([{ name: "会话数", data: [{ ts: 1000, value: 1 }, { ts: 2000, value: 2 }] }]) as never,
    );
    renderPage();
    expect(await screen.findByTestId("trend-chart")).toBeInTheDocument();
    expect(screen.getByText("会话数")).toBeInTheDocument();
  });

  it("切换窗口后按 window 重新查询", async () => {
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
