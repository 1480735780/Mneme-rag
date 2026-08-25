// M4C T8 任务 Tab：分页/状态过滤/上传触发/详情（含节点运行记录）
import { useCallback, useEffect, useState } from "react";
import { FileUp, ListTree, Search } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime, formatMs } from "@/shared/format";

import { getPipelinesPage, getTask, getTaskNodes, getTasksPage, uploadTaskFile } from "../api";
import { taskStatusMeta } from "../status";
import { TASK_STATUSES, type IngestionTask, type Pipeline, type TaskNode } from "../types";

const PAGE_SIZE = 10;

function UploadDialog({ open, onOpenChange, onUploaded }: { open: boolean; onOpenChange: (v: boolean) => void; onUploaded: () => void }) {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [pipelineId, setPipelineId] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    void getPipelinesPage(1, 50)
      .then((page) => {
        setPipelines(page.records);
        setPipelineId((prev) => prev || page.records[0]?.id || "");
      })
      .catch(() => setPipelines([]));
  }, [open]);

  const submit = async () => {
    if (!pipelineId || !file) return;
    setSubmitting(true);
    try {
      await uploadTaskFile(pipelineId, file);
      toast.success("任务已提交");
      onOpenChange(false);
      setFile(null);
      onUploaded();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>上传文档触发任务</DialogTitle>
          <DialogDescription>选择流水线与文件，将按流水线节点执行摄取。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label>流水线</Label>
            <Select value={pipelineId} onValueChange={(v) => setPipelineId(v ?? "")}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="选择流水线" />
              </SelectTrigger>
              <SelectContent>
                {pipelines.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="task-file">文件</Label>
            <Input id="task-file" type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !pipelineId || !file}>
            {submitting ? "提交中…" : "提交任务"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TaskDetailDialog({ task, open, onOpenChange }: { task: IngestionTask | null; open: boolean; onOpenChange: (v: boolean) => void }) {
  const [detail, setDetail] = useState<IngestionTask | null>(task);
  const [nodes, setNodes] = useState<TaskNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!task) return;
    setLoading(true);
    setError(null);
    try {
      const [d, ns] = await Promise.all([getTask(task.id), getTaskNodes(task.id)]);
      setDetail(d);
      setNodes(ns);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [task]);

  useEffect(() => {
    if (open && task) queueMicrotask(() => void load());
  }, [open, task, load]);

  const meta = taskStatusMeta(detail?.status);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] w-full max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>任务详情</DialogTitle>
          <DialogDescription>{detail?.id}</DialogDescription>
        </DialogHeader>
        {loading ? (
          <Loading label="加载任务详情…" />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : (
          <div className="grid gap-4 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div className="text-muted-foreground">流水线</div>
              <div className="font-mono text-xs">{detail?.pipelineId || "-"}</div>
              <div className="text-muted-foreground">来源</div>
              <div>
                {detail?.sourceFileName || detail?.sourceLocation || detail?.sourceType || "-"}
              </div>
              <div className="text-muted-foreground">状态</div>
              <div>
                <Badge variant={meta.value === "failed" ? "destructive" : meta.value === "completed" ? "default" : "secondary"}>
                  {meta.label}
                </Badge>
              </div>
              <div className="text-muted-foreground">分块数</div>
              <div>{detail?.chunkCount ?? "-"}</div>
              <div className="text-muted-foreground">创建时间</div>
              <div>{formatDateTime(detail?.createTime)}</div>
              <div className="text-muted-foreground">完成时间</div>
              <div>{formatDateTime(detail?.completedAt)}</div>
            </div>
            {detail?.errorMessage && (
              <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap break-all">{detail.errorMessage}</pre>
            )}
            <div>
              <div className="mb-2 flex items-center gap-1.5 font-medium">
                <ListTree className="size-4" />
                节点运行记录
              </div>
              {nodes.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无节点记录</p>
              ) : (
                <div className="grid gap-2">
                  {nodes.map((n) => {
                    const nMeta = taskStatusMeta(n.status);
                    return (
                      <div key={n.id} className="grid gap-1 rounded-lg border p-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex items-center gap-2 font-mono text-xs">
                            {n.nodeOrder != null && <span className="text-muted-foreground">{n.nodeOrder}.</span>}
                            {n.nodeId || "-"}
                            <Badge variant="secondary">{n.nodeType ?? "-"}</Badge>
                          </span>
                          <span className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Badge variant={nMeta.value === "failed" ? "destructive" : nMeta.value === "completed" ? "default" : "secondary"}>{nMeta.label}</Badge>
                            {formatMs(n.durationMs)}
                          </span>
                        </div>
                        {n.message && <p className="text-xs text-muted-foreground">{n.message}</p>}
                        {n.errorMessage && <pre className="overflow-x-auto rounded bg-muted p-1.5 text-xs whitespace-pre-wrap break-all">{n.errorMessage}</pre>}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function TasksTab() {
  const [records, setRecords] = useState<IngestionTask[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [detailTarget, setDetailTarget] = useState<IngestionTask | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getTasksPage(current, PAGE_SIZE, status || undefined);
      setRecords(page.records);
      setTotal(page.total);
      setPages(Math.max(1, Math.ceil(page.total / page.size)));
      if (page.total > 0 && current > Math.ceil(page.total / page.size)) {
        setCurrent(Math.ceil(page.total / page.size));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [current, status]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const onStatusChange = (v: string | null) => {
    setCurrent(1);
    setStatus(v ?? "");
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">状态</span>
            <Select value={status} onValueChange={onStatusChange}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder="全部状态" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">全部状态</SelectItem>
                {TASK_STATUSES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button variant="ghost" onClick={() => { setStatus(""); setCurrent(1); }}>
            <Search />
            重置
          </Button>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <FileUp />
          上传触发任务
        </Button>
      </div>

      {loading ? (
        <Loading label="加载任务…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无任务" description="上传文档或提交源后在此查看执行状态" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>任务 ID</TableHead>
                <TableHead>流水线</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>分块数</TableHead>
                <TableHead>创建时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((t) => {
                const meta = taskStatusMeta(t.status);
                return (
                  <TableRow key={t.id} className="cursor-pointer" onClick={() => setDetailTarget(t)}>
                    <TableCell className="font-mono text-xs text-muted-foreground">{t.id}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{t.pipelineId}</TableCell>
                    <TableCell className="max-w-48">
                      <span className="block truncate text-muted-foreground">{t.sourceFileName || t.sourceLocation || t.sourceType || "-"}</span>
                    </TableCell>
                    <TableCell>
                      <Badge variant={meta.value === "failed" ? "destructive" : meta.value === "completed" ? "default" : "secondary"}>{meta.label}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{t.chunkCount ?? "-"}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(t.createTime)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <div className="border-t px-2">
            <Pagination current={current} total={total} pages={pages} onChange={setCurrent} />
          </div>
        </div>
      )}

      {uploadOpen && <UploadDialog open onOpenChange={setUploadOpen} onUploaded={() => { setCurrent(1); void load(); }} />}

      {detailTarget && (
        <TaskDetailDialog task={detailTarget} open={Boolean(detailTarget)} onOpenChange={(v) => { if (!v) setDetailTarget(null); }} />
      )}
    </div>
  );
}
