// M3 追踪状态展示映射（对齐 trace_runner：RUNNING/SUCCESS/ERROR）

type Tone = "default" | "secondary" | "destructive" | "outline";

const TRACE_STATUS_META: Record<string, { label: string; tone: Tone }> = {
  SUCCESS: { label: "成功", tone: "default" },
  RUNNING: { label: "运行中", tone: "outline" },
  ERROR: { label: "失败", tone: "destructive" },
};

export function traceStatusMeta(status?: string | null) {
  return TRACE_STATUS_META[status ?? ""] ?? { label: status ?? "未知", tone: "secondary" as Tone };
}

/** 节点状态（与 run 同枚举；缺省同 SUCCESS 展示） */
export function traceNodeStatusMeta(status?: string | null) {
  return TRACE_STATUS_META[status ?? ""] ?? { label: status ?? "未知", tone: "secondary" as Tone };
}
