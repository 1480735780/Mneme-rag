// M4C T9 知识图谱页：标签搜索 + 子图可视化（自定义 SVG 圆形布局）+ LightRAG 引导
import { useCallback, useEffect, useState } from "react";
import { Network, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";

import { getGraph, getGraphLabels } from "../api";
import type { GraphEdge, GraphNode, GraphView } from "../types";

const VIEW_W = 760;
const VIEW_H = 520;
const CX = VIEW_W / 2;
const CY = VIEW_H / 2;
const RADIUS = Math.min(CX, CY) - 70;

/** 圆形布局：节点均匀分布在圆周上（确定性，便于测试与截图） */
function circleLayout(nodes: GraphNode[]): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  const n = nodes.length;
  nodes.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(1, n) - Math.PI / 2;
    positions[node.id] = { x: CX + RADIUS * Math.cos(angle), y: CY + RADIUS * Math.sin(angle) };
  });
  return positions;
}

const TYPE_COLORS: Record<string, string> = {
  entity: "#4f46e5",
  relationship: "#0891b2",
  document: "#16a34a",
  document_chunk: "#65a30d",
  keyword: "#9333ea",
  concept: "#ea580c",
};

function typeColor(type?: string): string {
  if (!type) return "#64748b";
  return TYPE_COLORS[type] ?? "#64748b";
}

export function GraphSvg({ nodes, edges }: { nodes: GraphNode[]; edges: GraphEdge[] }) {
  const positions = circleLayout(nodes);
  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      className="h-auto w-full"
      role="img"
      aria-label="知识图谱可视化"
      data-testid="graph-svg"
    >
      {/* 边 */}
      {edges.map((e) => {
        const s = positions[e.source];
        const t = positions[e.target];
        if (!s || !t) return null;
        return (
          <g key={e.id}>
            <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="#94a3b8" strokeWidth={1} />
            {e.label && (
              <text x={(s.x + t.x) / 2} y={(s.y + t.y) / 2 - 4} textAnchor="middle" className="fill-muted-foreground" fontSize={9}>
                {e.label}
              </text>
            )}
          </g>
        );
      })}
      {/* 节点 */}
      {nodes.map((node) => {
        const pos = positions[node.id];
        if (!pos) return null;
        const color = typeColor(node.type);
        return (
          <g key={node.id}>
            <circle cx={pos.x} cy={pos.y} r={16} fill={color} opacity={0.15} />
            <circle cx={pos.x} cy={pos.y} r={9} fill={color} stroke="#fff" strokeWidth={1.5} />
            <text x={pos.x} y={pos.y + 26} textAnchor="middle" className="fill-foreground" fontSize={10} fontWeight={500}>
              {node.name.length > 14 ? `${node.name.slice(0, 13)}…` : node.name}
            </text>
            {node.type && (
              <text x={pos.x} y={pos.y - 22} textAnchor="middle" className="fill-muted-foreground" fontSize={8}>
                {node.type}
              </text>
            )}
          </g>
        );
      })}
      {nodes.length === 0 && (
        <text x={CX} y={CY} textAnchor="middle" className="fill-muted-foreground" fontSize={13}>
          暂无图谱数据
        </text>
      )}
    </svg>
  );
}

const DEPTH_OPTIONS = [
  { value: "1", label: "1 层" },
  { value: "2", label: "2 层" },
  { value: "3", label: "3 层" },
];

export default function GraphPage() {
  const [entity, setEntity] = useState("");
  const [depth, setDepth] = useState("2");
  const [labels, setLabels] = useState<string[]>([]);
  const [graph, setGraph] = useState<GraphView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (keyword?: string) => {
    setLoading(true);
    setError(null);
    try {
      setGraph(await getGraph({ entity: keyword || undefined, depth: Number(depth), limit: 200 }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [depth]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  // 输入联想：拉取实体标签（防抖交给后端 limit）
  useEffect(() => {
    void getGraphLabels()
      .then(setLabels)
      .catch(() => setLabels([]));
  }, []);

  const onSearch = () => {
    void load(entity.trim() || undefined);
  };

  const isChannelDisabled = error?.includes("知识图谱通道未启用") || error?.includes("通道未启用");

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div>
        <h1 className="text-lg font-semibold">知识图谱</h1>
        <p className="text-sm text-muted-foreground">基于 LightRAG 的实体关系子图可视化</p>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div className="grid gap-1.5">
          <Label htmlFor="kg-entity">起始实体（留空取全图）</Label>
          <Input
            id="kg-entity"
            className="w-64"
            list="kg-labels"
            value={entity}
            onChange={(e) => setEntity(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSearch()}
            placeholder="搜索实体标签"
          />
          <datalist id="kg-labels">
            {labels.map((label) => (
              <option key={label} value={label} />
            ))}
          </datalist>
        </div>
        <div className="grid gap-1.5">
          <Label>深度</Label>
          <Select value={depth} onValueChange={(v) => setDepth(v ?? "2")}>
            <SelectTrigger className="w-28" aria-label="子图深度">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DEPTH_OPTIONS.map((d) => (
                <SelectItem key={d.value} value={d.value}>
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button onClick={onSearch}>
          <Search />
          查询子图
        </Button>
      </div>

      {loading ? (
        <Loading label="加载图谱…" />
      ) : error ? (
        isChannelDisabled ? (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-12 text-center">
            <Network className="text-muted-foreground size-8" />
            <div>
              <p className="font-medium">知识图谱通道未启用</p>
              <p className="mt-1 text-sm text-muted-foreground">
                当前未配置 LightRAG 服务。请在后端启用图谱通道（配置 LightRAG 连接后重启服务），即可在此查看实体关系子图。
              </p>
            </div>
          </div>
        ) : (
          <ErrorState message={error} onRetry={() => void load()} />
        )
      ) : !graph ? null : graph.nodes.length === 0 ? (
        <Empty title="暂无图谱数据" description="选择实体标签或留空查询全图" />
      ) : (
        <div className="grid gap-2">
          <div className="overflow-hidden rounded-lg border bg-card p-2">
            <GraphSvg nodes={graph.nodes} edges={graph.edges} />
          </div>
          <p className="text-xs text-muted-foreground">
            {graph.nodes.length} 个节点 · {graph.edges.length} 条边{graph.truncated ? "（结果已截断）" : ""}
          </p>
        </div>
      )}
    </div>
  );
}
