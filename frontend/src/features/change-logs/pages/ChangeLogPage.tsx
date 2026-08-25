// M4A T3 业务变更日志页：操作人/对象类型/操作类型/结果/时间过滤 + 分页 + 详情抽屉
import { useCallback, useEffect, useState } from "react";
import { Filter, History, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime } from "@/shared/format";

import { getChangeLog, getChangeLogsPage } from "../api";
import type { BizChangeLog } from "../types";

const PAGE_SIZE = 10;

function SuccessBadge({ success }: { success?: boolean | null }) {
  if (success === undefined || success === null) return <span className="text-muted-foreground">-</span>;
  return success ? <Badge>成功</Badge> : <Badge variant="destructive">失败</Badge>;
}

/** 详情字段行（snapshot 为 JSON 串，原样以等宽字体展示） */
function DetailField({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <div className="grid gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap break-all">{value}</pre>
    </div>
  );
}

function DetailDialog({ log, open, onOpenChange }: { log: BizChangeLog | null; open: boolean; onOpenChange: (v: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>变更日志详情</DialogTitle>
        </DialogHeader>
        {log ? (
          <div className="grid gap-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted-foreground">{log.id}</span>
              <Badge>{log.bizType ?? "-"}</Badge>
              <Badge variant="secondary">{log.operationType ?? "-"}</Badge>
              <SuccessBadge success={log.success} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="text-muted-foreground">操作人</div>
              <div>{log.operatorName || log.operatorId || "-"}</div>
              <div className="text-muted-foreground">角色</div>
              <div>{log.operatorRole || "-"}</div>
              <div className="text-muted-foreground">业务对象</div>
              <div className="font-mono text-xs">{log.bizId || "-"}</div>
              <div className="text-muted-foreground">时间</div>
              <div>{formatDateTime(log.createTime)}</div>
              <div className="text-muted-foreground">IP</div>
              <div>{log.ip || "-"}</div>
            </div>
            <DetailField label="操作描述" value={log.actionDesc} />
            {log.errorMessage && <DetailField label="错误信息" value={log.errorMessage} />}
            <DetailField label="变更前快照" value={log.beforeSnapshot} />
            <DetailField label="变更后快照" value={log.afterSnapshot} />
            <DetailField label="变更差异" value={log.changeDiff} />
            <DetailField label="调用位置" value={log.className ? `${log.className}.${log.methodName ?? ""}` : null} />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export default function ChangeLogPage() {
  const [records, setRecords] = useState<BizChangeLog[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [operatorInput, setOperatorInput] = useState("");
  const [bizInput, setBizInput] = useState("");
  const [opInput, setOpInput] = useState("");
  const [success, setSuccess] = useState<string>("");
  const [beginInput, setBeginInput] = useState("");
  const [endInput, setEndInput] = useState("");
  // 已提交的过滤条件（真源）
  const [filters, setFilters] = useState({ operatorId: "", bizType: "", operationType: "", success: "", beginTime: "", endTime: "" });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<BizChangeLog | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getChangeLogsPage({
        current,
        size: PAGE_SIZE,
        operatorId: filters.operatorId || undefined,
        bizType: filters.bizType || undefined,
        operationType: filters.operationType || undefined,
        success: filters.success ? filters.success === "true" : undefined,
        beginTime: filters.beginTime || undefined,
        endTime: filters.endTime || undefined,
      });
      setRecords(page.records);
      setTotal(page.total);
      const p = Math.max(1, Math.ceil(page.total / page.size));
      setPages(p);
      if (page.total > 0 && current > p) {
        setCurrent(p);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [current, filters]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const onSearch = () => {
    setCurrent(1);
    setFilters({
      operatorId: operatorInput.trim(),
      bizType: bizInput.trim(),
      operationType: opInput.trim(),
      success,
      beginTime: beginInput || "",
      endTime: endInput || "",
    });
  };

  const onReset = () => {
    setOperatorInput("");
    setBizInput("");
    setOpInput("");
    setSuccess("");
    setBeginInput("");
    setEndInput("");
    setCurrent(1);
    setFilters({ operatorId: "", bizType: "", operationType: "", success: "", beginTime: "", endTime: "" });
  };

  const openDetail = async (log: BizChangeLog) => {
    setDetail(log);
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await getChangeLog(log.id));
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : "加载详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div>
        <h1 className="text-lg font-semibold">业务变更日志</h1>
        <p className="text-sm text-muted-foreground">审计系统关键业务操作，支持按操作人、对象类型与时间过滤</p>
      </div>

      {/* 过滤条 */}
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">操作人</span>
          <Input className="w-40" value={operatorInput} onChange={(e) => setOperatorInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} placeholder="操作人/ID" />
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">对象类型</span>
          <Input className="w-40" value={bizInput} onChange={(e) => setBizInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} placeholder="如 KNOWLEDGE_BASE" />
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">操作类型</span>
          <Input className="w-32" value={opInput} onChange={(e) => setOpInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} placeholder="如 UPDATE" />
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">结果</span>
          <Select value={success} onValueChange={(v) => setSuccess(v ?? "")}>
            <SelectTrigger className="w-28">
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">全部</SelectItem>
              <SelectItem value="true">成功</SelectItem>
              <SelectItem value="false">失败</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">开始时间</span>
          <Input className="w-40" type="date" value={beginInput} onChange={(e) => setBeginInput(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <span className="text-xs text-muted-foreground">结束时间</span>
          <Input className="w-40" type="date" value={endInput} onChange={(e) => setEndInput(e.target.value)} />
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
        <Loading label="加载日志…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无变更日志" description="系统关键操作产生的审计记录将展示在这里" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>操作人</TableHead>
                <TableHead>对象类型</TableHead>
                <TableHead>操作</TableHead>
                <TableHead>描述</TableHead>
                <TableHead>结果</TableHead>
                <TableHead>时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((log) => (
                <TableRow key={log.id} className="cursor-pointer" onClick={() => void openDetail(log)}>
                  <TableCell>
                    <span className="flex items-center gap-1.5 font-medium">
                      <History className="text-muted-foreground size-4 shrink-0" />
                      {log.operatorName || log.operatorId || "-"}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{log.bizType ?? "-"}</Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{log.operationType ?? "-"}</TableCell>
                  <TableCell className="max-w-72">
                    <span className="block truncate text-muted-foreground">{log.actionDesc || "-"}</span>
                  </TableCell>
                  <TableCell>
                    <SuccessBadge success={log.success} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDateTime(log.createTime)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="border-t px-2">
            <Pagination current={current} total={total} pages={pages} onChange={setCurrent} />
          </div>
        </div>
      )}

      {detail && (
        <DetailDialog
          log={detail}
          open={Boolean(detail)}
          onOpenChange={(v) => {
            if (!v) setDetail(null);
          }}
        />
      )}
      {detailLoading && <Loading label="加载详情…" />}
      {detailError && <ErrorState message={detailError} />}
    </div>
  );
}
