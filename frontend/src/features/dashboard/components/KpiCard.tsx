// M3 Dashboard KPI 卡：总量 + 窗口增量 + 环比（deltaPct null → "-"，正负着色）
import { ArrowDownRight, ArrowUpRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatDeltaPct, formatNumber } from "@/shared/format";

import type { DashboardKpi } from "../types";

export function KpiCard({ title, kpi }: { title: string; kpi: DashboardKpi }) {
  const pct = formatDeltaPct(kpi?.deltaPct);
  const up = (kpi?.deltaPct ?? 0) > 0;
  return (
    <div className="rounded-lg border bg-card p-4">
      <p className="text-sm text-muted-foreground">{title}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{formatNumber(kpi?.value)}</p>
      <div className="mt-2 flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">
          {kpi?.delta === undefined || kpi?.delta === null ? "-" : `${kpi.delta > 0 ? "+" : ""}${formatNumber(kpi.delta)}`}
        </span>
        {pct !== "-" ? (
          <span
            className={cn(
              "flex items-center gap-0.5 font-medium",
              up ? "text-emerald-600" : "text-destructive",
            )}
          >
            {up ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}
            {pct}
          </span>
        ) : (
          <span className="text-muted-foreground">{pct}</span>
        )}
      </div>
    </div>
  );
}
