// M3 Dashboard REST API（对齐 admin/controller/dashboard_controller.py）
// 统一经 Axios interceptor 解包；响应为 camelCase VO（后端边界 camelize）
import { get } from "@/shared/api/client";

import type { DashboardOverview, DashboardPerformance, DashboardTrends, TrendMetric } from "./types";

/** GET /admin/dashboard/overview：总览六 KPI（window 可选，如 24h/7d） */
export function getDashboardOverview(window?: string): Promise<DashboardOverview> {
  return get("/admin/dashboard/overview", { params: { window: window || undefined } });
}

/** GET /admin/dashboard/performance：延迟/成功率/无文档/慢查询 */
export function getDashboardPerformance(window?: string): Promise<DashboardPerformance> {
  return get("/admin/dashboard/performance", { params: { window: window || undefined } });
}

/** GET /admin/dashboard/trends：day/hour 粒度序列（metric 为空 → 空 series） */
export function getDashboardTrends(params: {
  metric?: TrendMetric;
  window?: string;
  granularity?: string;
}): Promise<DashboardTrends> {
  return get("/admin/dashboard/trends", {
    params: {
      metric: params.metric || undefined,
      window: params.window || undefined,
      granularity: params.granularity || undefined,
    },
  });
}
