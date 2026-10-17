// M3 Dashboard 页：六 KPI + 性能指标 + 趋势图；window/metric/granularity 过滤同步 URL
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatMs, formatNumber, formatPercent, formatUsd } from "@/shared/format";

import { getDashboardCost, getDashboardOverview, getDashboardPerformance, getDashboardTrends } from "../api";
import { KpiCard } from "../components/KpiCard";
import { TrendChart } from "../components/TrendChart";
import type {
  CostCallRow,
  DashboardCost,
  DashboardCostDetail,
  DashboardOverview,
  DashboardPerformance,
  DashboardTrends,
} from "../types";

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

// ==================== 成本区（P10.3 T3.4，D14：KPI 三张 + 趋势两张 + trace 下钻） ====================

/** 成本 KPI 卡（无环比语义故不复用 KpiCard）：amount 为展示值（美元/百分比） */
function CostKpiCard({ title, amount, sub, warn }: { title: string; amount: string; sub: string; warn?: boolean }) {
  return (
    <div className="rounded-lg border bg-background p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">{title}</p>
        {/* 估算占比 >30% 出 P2 提示（承诺 8 前端呈现；预算告警归 T4.4 服务侧） */}
        {warn ? (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
            P2
          </span>
        ) : null}
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{amount}</p>
      <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
    </div>
  );
}

/** 计价徽标（快照 status=priced|unpriced；unpriced 带 reason 便于看出「无价」原因） */
function PricingBadge({ snapshot }: { snapshot?: Record<string, unknown> | null }) {
  const status = String(snapshot?.["status"] ?? "unpriced");
  if (status === "priced") {
    return (
      <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700">已计价</span>
    );
  }
  const reason = String(snapshot?.["reason"] ?? "");
  return (
    <span
      className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
      title={reason || undefined}
    >
      未定价{reason ? ` · ${reason}` : ""}
    </span>
  );
}

/** 明细行时间（服务端 naive 本地 ISO 串 → 浏览器本地展示，同机部署口径） */
function formatCallTime(v?: string | null): string {
  if (!v) return "-";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export default function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const window_ = readParam(searchParams, "window", "24h");
  const metric = readParam(searchParams, "metric", "sessions");
  const granularity = readParam(searchParams, "granularity", "hour");
  const traceId = readParam(searchParams, "traceId", "");

  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [perf, setPerf] = useState<DashboardPerformance | null>(null);
  const [trends, setTrends] = useState<DashboardTrends | null>(null);
  const [cost, setCost] = useState<DashboardCost | null>(null);
  const [costDetail, setCostDetail] = useState<DashboardCostDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(
    (patch: Record<string, string>) => {
      const next = new URLSearchParams(searchParams);
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v);
        else next.delete(k);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [o, p, t, c, d] = await Promise.all([
        getDashboardOverview(window_),
        getDashboardPerformance(window_),
        getDashboardTrends({ metric: metric as never, window: window_, granularity }),
        getDashboardCost(), // 成本 KPI 常驻（下钻时总览照旧——两张口径不互斥）
        traceId ? getDashboardCost(traceId) : Promise.resolve(null), // 下钻：?traceId= 明细
      ]);
      setOverview(o);
      setPerf(p);
      setTrends(t);
      // 成本双形态：总览常驻，下钻（?traceId=）催生的 detail 单独存放
      setCost(c as DashboardCost);
      setCostDetail(d ? (d as DashboardCostDetail) : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [window_, metric, granularity, traceId]);

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

          {/* 成本区（P10.3 T3.4：KPI 三张 + 趋势两张 + Top trace 下钻，D14） */}
          <section aria-label="成本区" className="grid gap-3 rounded-lg border bg-card p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">成本</h2>
              {cost ? (
                <span className="text-xs text-muted-foreground">
                  业务日 {cost.businessDay}
                  {cost.unpriced.rows > 0
                    ? ` · 未定价 ${cost.unpriced.rows} 行（${formatPercent(cost.unpriced.sharePct)}）`
                    : ""}
                </span>
              ) : null}
            </div>
            {cost ? (
              <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <CostKpiCard
                    title="当日成本"
                    amount={formatUsd(cost.kpis.costToday.valueUsd)}
                    sub={`${formatNumber(cost.kpis.costToday.calls)} 次调用${
                      cost.kpis.costToday.unpricedCalls > 0
                        ? ` · 未定价 ${cost.kpis.costToday.unpricedCalls} 行`
                        : ""
                    }`}
                  />
                  <CostKpiCard
                    title="当月成本"
                    amount={formatUsd(cost.kpis.costMonth.valueUsd)}
                    sub={`${formatNumber(cost.kpis.costMonth.calls)} 次调用`}
                  />
                  <CostKpiCard
                    title="估算占比"
                    amount={formatPercent(cost.kpis.estimatedShare.valuePct)}
                    sub={`${formatNumber(cost.kpis.estimatedShare.estimatedRows)} / ${formatNumber(
                      cost.kpis.estimatedShare.totalRows,
                    )} 行含估算 token`}
                    warn={cost.kpis.estimatedShare.valuePct > 30}
                  />
                </div>
                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="rounded-lg border">
                    <h3 className="px-3 pt-2 text-xs font-medium text-muted-foreground">
                      按日成本（近 7 个业务日）
                    </h3>
                    <TrendChart
                      granularity="day"
                      series={[{ name: "成本", data: cost.daily.map(({ ts, value }) => ({ ts, value })) }]}
                    />
                  </div>
                  <div className="rounded-lg border p-3">
                    <h3 className="text-xs font-medium text-muted-foreground">按模型（当月）</h3>
                    {cost.byModel.length > 0 ? (
                      <ul className="mt-3 space-y-2">
                        {cost.byModel.map((m) => {
                          const top = cost.byModel[0].costUsd || 1;
                          return (
                            <li key={m.model} className="flex items-center gap-2 text-xs">
                              <span className="w-28 truncate font-mono">{m.model}</span>
                              <span
                                className="h-2 min-w-[2%] rounded bg-blue-300"
                                style={{ width: `${Math.max(2, Math.round((m.costUsd / top) * 100))}%` }}
                              />
                              <span className="ml-auto tabular-nums">{formatUsd(m.costUsd)}</span>
                              <span className="w-10 text-right tabular-nums text-muted-foreground">
                                {m.calls} 次
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="mt-3 text-xs text-muted-foreground">暂无调用</p>
                    )}
                  </div>
                </div>
                <div>
                  <h3 className="text-xs font-medium text-muted-foreground">
                    当日下钻 · Top Trace（点击看调用明细）
                  </h3>
                  {cost.topTraces.length > 0 ? (
                    <ul className="mt-2 space-y-1">
                      {cost.topTraces.map((t) => (
                        <li key={t.traceId} className="flex items-center gap-2 text-xs">
                          <button
                            type="button"
                            className="font-mono underline-offset-2 hover:underline"
                            onClick={() => apply({ traceId: t.traceId })}
                          >
                            {t.traceId}
                          </button>
                          <span className="ml-auto tabular-nums">{formatUsd(t.costUsd)}</span>
                          <span className="w-10 text-right tabular-nums text-muted-foreground">
                            {t.calls} 次
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 text-xs text-muted-foreground">暂无下钻记录</p>
                  )}
                </div>
              </>
            ) : (
              <Empty title="暂无成本数据" description="成本记录将随调用发生" />
            )}
          </section>

          {traceId ? (
            <section aria-label="成本明细" className="rounded-lg border bg-card p-4">
              <div className="mb-2 flex items-center gap-2">
                <h2 className="text-sm font-semibold">调用明细 · {traceId}</h2>
                <button
                  type="button"
                  className="ml-auto text-xs text-muted-foreground underline-offset-2 hover:underline"
                  onClick={() => apply({ traceId: "" })}
                >
                  关闭下钻
                </button>
              </div>
              {costDetail ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs" data-testid="cost-call-table">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="py-1 pr-3 font-medium">时间</th>
                        <th className="pr-3 font-medium">#</th>
                        <th className="pr-3 font-medium">类型</th>
                        <th className="pr-3 font-medium">模型</th>
                        <th className="pr-3 font-medium">状态</th>
                        <th className="pr-3 font-medium">Tokens in/out</th>
                        <th className="pr-3 font-medium">延迟</th>
                        <th className="pr-3 font-medium">成本</th>
                        <th className="font-medium">计价</th>
                      </tr>
                    </thead>
                    <tbody>
                      {costDetail.calls.map((row: CostCallRow) => (
                        <tr key={row.id} className="border-t">
                          <td className="py-1 pr-3 tabular-nums">{formatCallTime(row.createTime)}</td>
                          <td className="pr-3 tabular-nums">{row.attemptNo ?? "-"}</td>
                          <td className="pr-3">{row.callKind ?? "-"}</td>
                          <td className="pr-3 font-mono">{row.model ?? "-"}</td>
                          <td className="pr-3">{row.status ?? "-"}</td>
                          <td className="pr-3 tabular-nums">
                            {formatNumber(row.promptTokens)} / {formatNumber(row.completionTokens)}
                          </td>
                          <td className="pr-3 tabular-nums">{formatMs(row.latencyMs)}</td>
                          <td className="pr-3 tabular-nums">{formatUsd(row.costUsd)}</td>
                          <td>
                            <PricingBadge snapshot={row.pricingSnapshot} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Loading label="加载明细。" />
              )}
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
