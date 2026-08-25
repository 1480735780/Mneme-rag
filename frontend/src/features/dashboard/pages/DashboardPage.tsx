// M3 Dashboard 页：六 KPI + 性能指标 + 趋势图；window/metric/granularity 过滤同步 URL
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatMs, formatPercent } from "@/shared/format";

import { getDashboardOverview, getDashboardPerformance, getDashboardTrends } from "../api";
import { KpiCard } from "../components/KpiCard";
import { TrendChart } from "../components/TrendChart";
import type { DashboardOverview, DashboardPerformance, DashboardTrends } from "../types";

const WINDOWS = [
  { value: "24h", label: "近 24 小时" },
  { value: "7d", label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
];

const METRICS = [
  { value: "sessions", label: "会话数" },
  { value: "messages", label: "消息数" },
  { value: "activeusers", label: "活跃用户" },
  { value: "avglatency", label: "平均延迟" },
  { value: "quality", label: "质量" },
];

const GRANULARITIES = [
  { value: "hour", label: "按小时" },
  { value: "day", label: "按天" },
];

function readParam(sp: URLSearchParams, key: string, fallback: string): string {
  const v = sp.get(key);
  return v ? v : fallback;
}

function MetricChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}

export default function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const window_ = readParam(searchParams, "window", "24h");
  const metric = readParam(searchParams, "metric", "sessions");
  const granularity = readParam(searchParams, "granularity", "hour");

  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [perf, setPerf] = useState<DashboardPerformance | null>(null);
  const [trends, setTrends] = useState<DashboardTrends | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(
    (patch: Record<string, string>) => {
      const next = new URLSearchParams(searchParams);
      for (const [k, v] of Object.entries(patch)) next.set(k, v);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [o, p, t] = await Promise.all([
        getDashboardOverview(window_),
        getDashboardPerformance(window_),
        getDashboardTrends({ metric: metric as never, window: window_, granularity }),
      ]);
      setOverview(o);
      setPerf(p);
      setTrends(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [window_, metric, granularity]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  const kpis = overview?.kpis;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">仪表盘</h1>
          <p className="text-sm text-muted-foreground">用户 / 会话 / 消息 KPI 与链路性能概览</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={window_} onValueChange={(v) => apply({ window: v ?? "24h" })}>
            <SelectTrigger className="w-36" aria-label="时间窗口">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOWS.map((w) => (
                <SelectItem key={w.value} value={w.value}>
                  {w.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {loading ? (
        <Loading label="加载仪表盘…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : kpis ? (
        <>
          {/* 六 KPI */}
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <KpiCard title="总用户" kpi={kpis.totalUsers} />
            <KpiCard title="活跃用户" kpi={kpis.activeUsers} />
            <KpiCard title="总会话" kpi={kpis.totalSessions} />
            <KpiCard title="窗口会话" kpi={kpis.sessions24h} />
            <KpiCard title="总消息" kpi={kpis.totalMessages} />
            <KpiCard title="窗口消息" kpi={kpis.messages24h} />
          </section>

          {/* 性能 */}
          <section className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <MetricChip label="平均延迟" value={perf ? formatMs(perf.avgLatencyMs) : "-"} />
            <MetricChip label="P95 延迟" value={perf ? formatMs(perf.p95LatencyMs) : "-"} />
            <MetricChip label="成功率" value={perf ? formatPercent(perf.successRate) : "-"} />
            <MetricChip label="错误率" value={perf ? formatPercent(perf.errorRate) : "-"} />
            <MetricChip label="无知识率" value={perf ? formatPercent(perf.noDocRate) : "-"} />
            <MetricChip label="慢查询率" value={perf ? formatPercent(perf.slowRate) : "-"} />
          </section>

          {/* 趋势 */}
          <section className="grid gap-3 rounded-lg border bg-card p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">趋势</h2>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <Select value={metric} onValueChange={(v) => apply({ metric: v ?? "sessions" })}>
                  <SelectTrigger className="w-32" aria-label="指标">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {METRICS.map((m) => (
                      <SelectItem key={m.value} value={m.value}>
                        {m.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={granularity} onValueChange={(v) => apply({ granularity: v ?? "hour" })}>
                  <SelectTrigger className="w-28" aria-label="粒度">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {GRANULARITIES.map((g) => (
                      <SelectItem key={g.value} value={g.value}>
                        {g.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {trends && trends.series.length > 0 ? (
              <TrendChart series={trends.series} granularity={granularity} />
            ) : (
              <Empty title="暂无趋势数据" description="当前窗口无满足条件的数据" />
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
