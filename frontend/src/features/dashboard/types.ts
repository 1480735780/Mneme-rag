// M3 Dashboard 域类型（对齐 admin/service/dashboard_service.py 输出 + controller camelize）
// 注意：kpis 的 snake_case key 经后端 camelize 转 camelCase（total_users → totalUsers）

/** KPI 单值（value 总量 / delta 窗口增量 / deltaPct 环比，prev≤0 时为 null） */
export interface DashboardKpi {
  value: number;
  delta: number;
  deltaPct: number | null;
}

/** 总览六 KPI（camelCase） */
export interface DashboardOverview {
  window?: string | null;
  compareWindow?: string | null;
  updatedAt?: number | null;
  kpis: {
    totalUsers: DashboardKpi;
    activeUsers: DashboardKpi;
    totalSessions: DashboardKpi;
    sessions24h: DashboardKpi;
    totalMessages: DashboardKpi;
    messages24h: DashboardKpi;
  };
}

/** 性能指标（camelCase：延迟 ms / 速率 0-100 一位小数） */
export interface DashboardPerformance {
  window?: string | null;
  avgLatencyMs?: number | null;
  p95LatencyMs?: number | null;
  successRate?: number | null;
  errorRate?: number | null;
  noDocRate?: number | null;
  slowRate?: number | null;
}

/** 趋势序列点（ts 为毫秒时间戳） */
export interface TrendPoint {
  ts: number;
  value: number;
}

/** 趋势序列（多序列，如 quality → 错误率 + 无知识率） */
export interface TrendSeries {
  name: string;
  data: TrendPoint[];
}

/** 趋势响应 */
export interface DashboardTrends {
  metric?: string | null;
  window?: string | null;
  granularity?: string | null;
  series: TrendSeries[];
}

/** 趋势可选指标 */
export type TrendMetric = "sessions" | "messages" | "activeusers" | "avglatency" | "quality";
