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

// ==================== 成本区（P10.3 T3.3/T3.4：GET /admin/dashboard/cost，camelCase） ====================

/** 成本 KPI 卡（当日/当月）：valueUsd 已固化成本合计（unpriced 行不贡献金额，不伪造 0）；unpricedCalls 该窗未定价行数 */
export interface CostKpiValue {
  valueUsd: number;
  calls: number;
  unpricedCalls: number;
}

/** 估算占比 KPI（D6 一等公民）：token_source 非 real（estimated/mixed）行占比；>30% 出 P2 提示 */
export interface CostEstimatedShare {
  valuePct: number;
  estimatedRows: number;
  totalRows: number;
}

/** 按日成本点：近 7 个业务日；ts 为东八区 00:00 绝对时刻（epoch ms），unpriced 为该日未定价行数 */
export interface CostDailyPoint {
  ts: number;
  value: number;
  date: string;
  calls: number;
  unpriced: number;
}

/** 按模型分布（当月口径）：unpriced 行零金额但计入 calls——「进行了哪些调用」是事实本身 */
export interface CostModelShare {
  model: string;
  costUsd: number;
  calls: number;
}

/** 当日成本 Top trace（下钻入口） */
export interface CostTopTrace {
  traceId: string;
  costUsd: number;
  calls: number;
}

/** 调用明细行（trace 下钻投影；pricingSnapshot 已解析为 dict，status=priced|unpriced） */
export interface CostCallRow {
  id: string;
  requestUid?: string | null;
  traceId?: string | null;
  callKind?: string | null;
  scene?: string | null;
  purpose?: string | null;
  tier?: string | null;
  provider?: string | null;
  model?: string | null;
  status?: string | null;
  errorType?: string | null;
  attemptNo?: number | null;
  isFinal?: number | null;
  promptTokens?: number | null;
  completionTokens?: number | null;
  tokenSource?: string | null;
  latencyMs?: number | null;
  ttftMs?: number | null;
  costUsd?: number | null;
  pricingSnapshot?: Record<string, unknown> | null;
  createTime?: string | null;
}

/** GET /admin/dashboard/cost 应答（无 traceId：KPI/趋势/分布） */
export interface DashboardCost {
  updatedAt?: number | null;
  businessDay?: string | null;
  kpis: {
    costToday: CostKpiValue;
    costMonth: CostKpiValue;
    estimatedShare: CostEstimatedShare;
  };
  unpriced: { sharePct: number; rows: number; totalRows: number };
  daily: CostDailyPoint[];
  byModel: CostModelShare[];
  topTraces: CostTopTrace[];
}

/** GET /admin/dashboard/cost?traceId= 应答（下钻：一次请求链路的调用明细） */
export interface DashboardCostDetail {
  traceId: string;
  updatedAt?: number | null;
  calls: CostCallRow[];
}

/** 成本应答的窄化守卫：响应含 calls 数组即下钻形态 */
export function isCostDetail(
  data: DashboardCost | DashboardCostDetail,
): data is DashboardCostDetail {
  return Array.isArray((data as DashboardCostDetail).calls);
}
