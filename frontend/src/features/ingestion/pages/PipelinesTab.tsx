// M4C T8 流水线 Tab：分页/搜索/创建/编辑（含节点编辑器）/删除（二次确认）
import { useCallback, useEffect, useState } from "react";
import { GitBranch, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime } from "@/shared/format";

import { createPipeline, deletePipeline, getPipelinesPage, updatePipeline } from "../api";
import { PIPELINE_NODE_TYPES, type Pipeline, type PipelineNode } from "../types";

const PAGE_SIZE = 10;

/** 节点编辑器行（nodeId/nextNodeId 文本 + nodeType 下拉；settings 可选 JSON） */
interface NodeRow {
  key: string;
  nodeId: string;
  nodeType: string;
  nextNodeId: string;
  settingsText: string;
}

function toNodeRow(n: PipelineNode): NodeRow {
  return {
    key: `${n.nodeId}-${Math.random().toString(36).slice(2, 8)}`,
    nodeId: n.nodeId,
    nodeType: n.nodeType,
    nextNodeId: n.nextNodeId ?? "",
    settingsText: n.settings ? JSON.stringify(n.settings, null, 2) : "",
  };
}

function PipelineDialog({
  target,
  open,
  onOpenChange,
  onSaved,
}: {
  target: Pipeline | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const isEdit = Boolean(target);
  const [name, setName] = useState(target?.name ?? "");
  const [description, setDescription] = useState(target?.description ?? "");
  const [rows, setRows] = useState<NodeRow[]>((target?.nodes ?? []).map(toNodeRow));
  const [submitting, setSubmitting] = useState(false);

  const updateRow = (key: string, patch: Partial<NodeRow>) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  };

  const addRow = () => {
    setRows((prev) => [
      ...prev,
      { key: `n-${Math.random().toString(36).slice(2, 8)}`, nodeId: `node_${prev.length + 1}`, nodeType: "parser", nextNodeId: "", settingsText: "" },
    ]);
  };

  const removeRow = (key: string) => {
    setRows((prev) => prev.filter((r) => r.key !== key));
  };

  const submit = async () => {
    if (!name.trim()) return;
    const nodes: PipelineNode[] = rows
      .filter((r) => r.nodeId.trim())
      .map((r) => ({
        nodeId: r.nodeId.trim(),
        nodeType: r.nodeType,
        nextNodeId: r.nextNodeId.trim() || undefined,
        settings: r.settingsText.trim() ? (parseJson(r.settingsText) as Record<string, unknown>) : undefined,
      }));
    if (rows.some((r) => r.settingsText.trim() && !parseJson(r.settingsText))) {
      toast.error("存在非法的节点设置 JSON");
      return;
    }
    setSubmitting(true);
    try {
      const payload = { name: name.trim(), description: description.trim() || undefined, nodes };
      if (isEdit && target) {
        await updatePipeline(target.id, payload);
        toast.success("已保存");
      } else {
        await createPipeline(payload);
        toast.success("已创建");
      }
      onOpenChange(false);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] w-full max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑流水线" : "新建流水线"}</DialogTitle>
          <DialogDescription>定义处理节点的顺序与类型，驱动文档摄取。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="pl-name">名称</Label>
            <Input id="pl-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：默认摄取流水线" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="pl-desc">描述</Label>
            <Input id="pl-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选" />
          </div>
          <div className="grid gap-1.5">
            <div className="flex items-center justify-between">
              <Label>节点</Label>
              <Button variant="outline" size="sm" onClick={addRow}>
                <Plus />
                添加节点
              </Button>
            </div>
            <div className="grid gap-2">
              {rows.length === 0 && <p className="text-sm text-muted-foreground">暂无节点，点击「添加节点」开始编排。</p>}
              {rows.map((row) => (
                <div key={row.key} className="grid gap-2 rounded-lg border p-3">
                  <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-2">
                    <div className="grid gap-1">
                      <Label>节点 ID</Label>
                      <Input value={row.nodeId} onChange={(e) => updateRow(row.key, { nodeId: e.target.value })} placeholder="如 fetch_1" />
                    </div>
                    <div className="grid gap-1">
                      <Label>类型</Label>
                      <Select value={row.nodeType} onValueChange={(v) => updateRow(row.key, { nodeType: v ?? "parser" })}>
                        <SelectTrigger className="w-28">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PIPELINE_NODE_TYPES.map((t) => (
                            <SelectItem key={t.value} value={t.value}>
                              {t.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid gap-1">
                      <Label>下一节点</Label>
                      <Input value={row.nextNodeId} onChange={(e) => updateRow(row.key, { nextNodeId: e.target.value })} placeholder="可选" />
                    </div>
                  </div>
                  <div className="flex items-end gap-2">
                    <div className="grid flex-1 gap-1">
                      <Label>设置（JSON，可选）</Label>
                      <Input value={row.settingsText} onChange={(e) => updateRow(row.key, { settingsText: e.target.value })} placeholder='如 {"maxChunkSize": 512}' />
                    </div>
                    <Button variant="ghost" size="sm" className="text-destructive" onClick={() => removeRow(row.key)}>
                      <Trash2 />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !name.trim()}>
            {submitting ? "保存中…" : isEdit ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export default function PipelinesTab() {
  const [records, setRecords] = useState<Pipeline[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [name, setName] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ open: boolean; target: Pipeline | null }>({ open: false, target: null });
  const [deleteTarget, setDeleteTarget] = useState<Pipeline | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getPipelinesPage(current, PAGE_SIZE, keyword || undefined);
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
  }, [current, keyword]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const onSearch = () => {
    setCurrent(1);
    setKeyword(name.trim());
  };

  const doDelete = async (p: Pipeline) => {
    try {
      await deletePipeline(p.id);
      toast.success("已删除");
      setDeleteTarget(null);
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input className="w-56 pl-8" placeholder="搜索流水线" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onSearch()} />
          </div>
          <Button variant="outline" onClick={onSearch}>
            搜索
          </Button>
        </div>
        <Button onClick={() => setDialog({ open: true, target: null })}>
          <Plus />
          新建流水线
        </Button>
      </div>

      {loading ? (
        <Loading label="加载流水线…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无流水线" description="点击「新建流水线」定义第一条处理流程" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>描述</TableHead>
                <TableHead>节点数</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="font-medium">
                    <span className="flex items-center gap-2">
                      <GitBranch className="text-muted-foreground size-4" />
                      {p.name}
                    </span>
                  </TableCell>
                  <TableCell className="max-w-64">
                    <span className="block truncate text-muted-foreground">{p.description || "-"}</span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{p.nodes?.length ?? 0}</Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDateTime(p.updateTime ?? p.createTime)}</TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                        <MoreHorizontal />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => setDialog({ open: true, target: p })}>
                          <Pencil />
                          编辑
                        </DropdownMenuItem>
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(p)}>
                          <Trash2 />
                          删除
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="border-t px-2">
            <Pagination current={current} total={total} pages={pages} onChange={setCurrent} />
          </div>
        </div>
      )}

      {dialog.open && (
        <PipelineDialog
          target={dialog.target}
          open
          onOpenChange={(v) => setDialog((d) => ({ ...d, open: v }))}
          onSaved={() => {
            if (!dialog.target) setCurrent(1);
            void load();
          }}
        />
      )}

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除流水线</AlertDialogTitle>
            <AlertDialogDescription>确定删除「{deleteTarget?.name}」吗？该操作不可恢复。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => deleteTarget && void doDelete(deleteTarget)}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
