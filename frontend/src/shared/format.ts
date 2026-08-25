// M3 通用格式化工具（trace 耗时/百分比、dashboard 数值共用）
// 对齐后端 VO：duration_ms 为整数毫秒；速率字段为 0-100 一位小数

/** ISO 时间 → 本地展示；空值返回 "-" */
export function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 字节 → 人类可读（B/KB/MB/GB） */
export function formatFileSize(bytes?: number | null): string {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return "-";
  const b = Number(bytes);
  if (b < 1024) return `${b} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = b / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** 毫秒 → 可读耗时；空值返回 "-"（<1s 显示 ms，≥1s 显示 s） */
export function formatMs(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(1)}s`;
}

/** 速率（0-100）→ 百分比；空值返回 "-" */
export function formatPercent(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  return `${Number(v).toFixed(1)}%`;
}

/** 绝对值 → 千分位分隔（dashboard KPI 大数） */
export function formatNumber(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  return Number(v).toLocaleString("zh-CN");
}

/** 环比百分比 → 带符号文本；null/undefined 返回 "-"（prev≤0 后端置 null） */
export function formatDeltaPct(v?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}
