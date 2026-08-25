// M3 T2 链路追踪详情页：run 概要卡 + 节点时间线（顺序/深度缩进/耗时占比条/错误展开）
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ArrowUpRight, CircleAlert, GitBranch, MessageSquare } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime, formatMs } from "@/shared/format";

import { getTraceDetail } from "../api";
import { traceNodeStatusMeta, traceStatusMeta } from "../status";
import type { TraceNode, TraceRun } from "../types";

/** 节点耗时占比条宽度（相对该 run 最大节点耗时） */
function barWidth(duration: number | null | undefined, max: number): number {
  if (!duration || max <= 0) return 0;
  return Math.max(4, Math.round((duration / max) * 100));
}

function RunSummary({ run }: { run: TraceRun }) {
  const meta = traceStatusMeta(run.status);
  return (
    <div className="grid gap-4 rounded-lg border bg-card p-4 sm:grid-cols-2 lg:grid-cols-4">
      <div className="sm:col-span-2">
        <p className="text-sm font-medium">{run.question || "（无问题）"}</p>
        <p className="mt-1 font-mono text-xs text-muted-foreground">{run.traceId}</p>
        <div className="mt-2">
          <Badge variant={meta.tone}>{meta.label}</Badge>
        </div>
      </div>
      <div className="grid gap-2 text-sm">
        <div className="flex justify-between gap-2">
          <span className="text-muted-foreground">耗时</span>
          <span>{formatMs(run.durationMs)}</span>
        </div>
        <div className="flex justify-between gap-2">
          <span className="text-muted-foreground">TTFT</span>
          <span>{formatMs(run.ttftMs)}</span>
        </div>
        <div className="flex justify-between gap-2">
          <span className="text-muted-foreground">用户</span>
          <span>{run.username || run.userId || "-"}</span>
        </div>
      </div>
      <div className="grid gap-2 text-sm">
        <div className="flex justify-between gap-2">
          <span className="text-muted-foreground">开始</span>
          <span>{formatDateTime(run.startTime)}</span>
        </div>
        <div className="flex justify-between gap-2">
          <span className="text-muted-foreground">结束</span>
          <span>{formatDateTime(run.endTime)}</span>
        </div>
        {run.conversationId && (
          <Button variant="outline" size="sm" nativeButton={false} render={<Link to={`/chat/${run.conversationId}`} />}>
            <MessageSquare />
            查看会话
            <ArrowUpRight />
          </Button>
        )}
      </div>
    </div>
  );
}

function NodeTimeline({ nodes }: { nodes: TraceNode[] }) {
  const maxDuration = useMemo(
    () => Math.max(0, ...nodes.map((n) => n.durationMs ?? 0)),
    [nodes],
  );
  if (nodes.length === 0) {
    return <Empty title="暂无节点" description="该 run 未记录到节点" />;
  }
  return (
    <div className="flex flex-col gap-1.5">
      {nodes.map((node) => {
        const meta = traceNodeStatusMeta(node.status);
        const width = barWidth(node.durationMs, maxDuration);
        return (
          <div
            key={node.nodeId}
            className="rounded-lg border bg-card p-3"
            style={{ marginLeft: Math.min((node.depth ?? 0), 6) * 20 }}
          >
            <div className="flex flex-wrap items-center gap-2">
              <GitBranch className="text-muted-foreground size-4 shrink-0" />
              <span className="text-sm font-medium">{node.nodeName || node.nodeType || node.nodeId}</span>
              {node.nodeType && (
                <span className="text-xs text-muted-foreground">{node.nodeType}</span>
              )}
              <Badge variant={meta.tone}>{meta.label}</Badge>
              <span className="ml-auto font-mono text-xs text-muted-foreground">{formatMs(node.durationMs)}</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: `${width}%` }} />
            </div>
            {node.errorMessage && (
              <div className="mt-2 flex items-start gap-1.5 rounded-md bg-destructive/10 p-2 text-xs text-destructive">
                <CircleAlert className="mt-0.5 size-3.5 shrink-0" />
                <span className="break-all">{node.errorMessage}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function TraceDetailPage() {
  const { traceId = "" } = useParams();
  const [detail, setDetail] = useState<{ run: TraceRun; nodes: TraceNode[] } | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setNotFound(false);
    try {
      const data = await getTraceDetail(traceId);
      if (data === null) {
        setNotFound(true);
      } else {
        setDetail(data);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [traceId]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon-sm" nativeButton={false} render={<Link to="/admin/traces" />} aria-label="返回追踪列表">
          <ArrowLeft />
        </Button>
        <div>
          <h1 className="text-lg font-semibold">追踪详情</h1>
          <p className="font-mono text-xs text-muted-foreground">{traceId}</p>
        </div>
      </div>

      {loading ? (
        <Loading label="加载追踪…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : notFound ? (
        <Empty title="追踪不存在" description="该 traceId 未找到运行记录，可能已删除" />
      ) : detail ? (
        <>
          <RunSummary run={detail.run} />
          <div className="grid gap-2">
            <h2 className="text-sm font-semibold text-muted-foreground">节点时间线（{detail.nodes.length}）</h2>
            <NodeTimeline nodes={detail.nodes} />
          </div>
        </>
      ) : null}
    </div>
  );
}
