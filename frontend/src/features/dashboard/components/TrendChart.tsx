// M3 Dashboard 趋势折线图（recharts，固定尺寸避免 jsdom ResponsiveContainer 0 宽问题）
// 输入多序列（quality → 错误率+无知识率两线），序列时间点对齐（同 start/end/step）
import { CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";

import type { TrendSeries } from "../types";

interface TrendChartProps {
  series: TrendSeries[];
  granularity?: string;
  height?: number;
}

function formatTick(ts: number, granularity?: string): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (granularity === "day") {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 多序列合并为统一 X 轴数据点（各序列 index 对齐） */
function toChartData(series: TrendSeries[]): Record<string, number | string>[] {
  if (series.length === 0) return [];
  const first = series[0];
  return first.data.map((point, i) => {
    const row: Record<string, number | string> = { ts: point.ts };
    for (const s of series) {
      row[s.name] = s.data[i]?.value ?? 0;
    }
    return row;
  });
}

export function TrendChart({ series, granularity, height = 260 }: TrendChartProps) {
  if (series.length === 0) return null;
  const data = toChartData(series);
  const colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b"];
  return (
    <div className="overflow-x-auto" data-testid="trend-chart">
      <LineChart width={720} height={height} data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" vertical={false} />
        <XAxis
          dataKey="ts"
          tickFormatter={(v) => formatTick(Number(v), granularity)}
          tick={{ fontSize: 11 }}
          minTickGap={32}
        />
        <YAxis tick={{ fontSize: 11 }} width={44} />
        <Tooltip
          labelFormatter={(v) => new Date(Number(v)).toLocaleString("zh-CN")}
          formatter={(value) => [Number(value).toFixed(1), ""]}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {series.map((s, i) => (
          <Line
            key={s.name}
            type="monotone"
            dataKey={s.name}
            stroke={colors[i % colors.length]}
            dot={false}
            strokeWidth={2}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </div>
  );
}
