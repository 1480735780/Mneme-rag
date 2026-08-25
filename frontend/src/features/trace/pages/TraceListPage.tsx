// M3 T1 链路追踪列表页：traceId/conversationId/taskId/status 过滤 + 分页 + URL query 同步
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Clock, Filter, Search, Waypoints } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime, formatMs } from "@/shared/format";

import { getTraceRunsPage } from "../api";
import { traceStatusMeta } from "../status";
import type { TraceRun } from "../types";

const PAGE_SIZE = 10;

function readParam(sp: URLSearchParams, key: string): string {
  return sp.get(key) ?? "";
}

export default function TraceListPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // URL query 为过滤条件真源（完成标准 4：过滤条件同步到 URL）
  const current = Math.max(1, Number(readParam(searchParams, "current") || "1"));
  const traceId = readParam(searchParams, "traceId");
  const conversationId = readParam(searchParams, "conversationId");
  const taskId = readParam(searchParams, "taskId");
  const status = readParam(searchParams, "status");

  // 输入框临时值（仅提交时写入 URL，避免按键触发请求）
  const [traceInput, setTraceInput] = useState(traceId);
  const [convInput, setConvInput] = useState(conversationId);
  const [taskInput, setTaskInput] = useState(taskId);

  const [records, setRecords] = useState<TraceRun[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const applyQuery = useCallback(
    (patch: Record<string, string | undefined>) => {
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
      const page = await getTraceRunsPage({
        current,
        size: PAGE_SIZE,
        traceId: traceId || undefined,
        conversationId: conversationId || undefined,
        taskId: taskId || undefined,
        status: status || undefined,
      });
      setRecords(page.records);
      setTotal(page.total);
      setPages(Math.max(1, Math.ceil(page.total / page.size)));
      if (page.total > 0 && current > Math.ceil(page.total / page.size)) {
        applyQuery({ current: undefined });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [current, traceId, conversationId, taskId, status, applyQuery]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  const onSearch = () => {
    applyQuery({ current: undefined, traceId: traceInput || undefined, conversationId: convInput || undefined, taskId: taskInput || undefined });
  };

  const onStatusChange = (v: string | null) => {
    applyQuery({ current: undefined, status: v || undefined });
  };

  const onReset = () => {
    setTraceInput("");
    setConvInput("");
    setTaskInput("");
    applyQuery({ current: undefined, traceId: undefined, conversationId: undefined, taskId: undefined, status: undefined });
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold">链路追踪</h1>
          <p className="text-sm text-muted-foreground">一次 Chat 对应一条 run，可定位各节点耗时与错误</p>
        </div>
      </div>

      {/* 过滤条 */}
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">traceId</span>
          <Input className="w-48" value={traceInput} onChange={(e) => setTraceInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} placeholder="按 traceId 过滤" />
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">会话 ID</span>
          <Input className="w-48" value={convInput} onChange={(e) => setConvInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} placeholder="按 conversationId" />
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">任务 ID</span>
          <Input className="w-48" value={taskInput} onChange={(e) => setTaskInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} placeholder="按 taskId" />
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">状态</span>
          <Select value={status} onValueChange={onStatusChange}>
            <SelectTrigger className="w-32">
              <SelectValue placeholder="全部状态" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">全部状态</SelectItem>
              <SelectItem value="SUCCESS">成功</SelectItem>
              <SelectItem value="RUNNING">运行中</SelectItem>
              <SelectItem value="ERROR">失败</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button onClick={onSearch}>
          <Search />
          搜索
        </Button>
        <Button variant="ghost" onClick={onReset}>
          <Filter />
          重置
        </Button>
      </div>

      {loading ? (
        <Loading label="加载追踪…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无追踪记录" description="完成一次 Chat 问答后在此查看链路" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>问题</TableHead>
                <TableHead>traceId</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>耗时</TableHead>
                <TableHead>TTFT</TableHead>
                <TableHead>用户</TableHead>
                <TableHead>开始时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((run) => {
                const meta = traceStatusMeta(run.status);
                return (
                  <TableRow key={run.traceId} className="cursor-pointer" onClick={() => navigate(`/admin/traces/${run.traceId}`)}>
                    <TableCell className="max-w-56">
                      <span className="flex items-center gap-1.5">
                        <Waypoints className="text-muted-foreground size-4 shrink-0" />
                        <span className="truncate font-medium">{run.question || "-"}</span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="font-mono text-xs text-muted-foreground">{run.traceId}</span>
                    </TableCell>
                    <TableCell>
                      <Badge variant={meta.tone}>{meta.label}</Badge>
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1 text-muted-foreground">
                        <Clock className="size-3.5" />
                        {formatMs(run.durationMs)}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatMs(run.ttftMs)}</TableCell>
                    <TableCell className="text-muted-foreground">{run.username || run.userId || "-"}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(run.startTime)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <div className="border-t px-2">
            <Pagination current={current} total={total} pages={pages} onChange={(p) => applyQuery({ current: String(p) })} />
          </div>
        </div>
      )}
    </div>
  );
}
