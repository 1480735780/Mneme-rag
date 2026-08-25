// M4B T5 术语映射管理页：分页/搜索/创建/编辑（启用开关/优先级）/删除（二次确认）
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime } from "@/shared/format";

import { createTermMapping, deleteTermMapping, getTermMappingsPage, updateTermMapping } from "../api";
import type { TermMapping } from "../types";

const PAGE_SIZE = 10;

function MappingDialog({
  target,
  open,
  onOpenChange,
  onSaved,
}: {
  target: TermMapping | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [sourceTerm, setSourceTerm] = useState(target?.sourceTerm ?? "");
  const [targetTerm, setTargetTerm] = useState(target?.targetTerm ?? "");
  const [priority, setPriority] = useState(String(target?.priority ?? 0));
  const [enabled, setEnabled] = useState(target?.enabled ?? true);
  const [remark, setRemark] = useState(target?.remark ?? "");
  const [submitting, setSubmitting] = useState(false);
  const isEdit = Boolean(target);

  const submit = async () => {
    if (!sourceTerm.trim() || !targetTerm.trim()) return;
    setSubmitting(true);
    try {
      const payload = {
        source_term: sourceTerm.trim(),
        target_term: targetTerm.trim(),
        priority: Number.isNaN(Number(priority)) ? 0 : Number(priority),
        enabled,
        remark: remark.trim() || undefined,
      };
      if (target?.id) {
        await updateTermMapping(target.id, payload);
        toast.success("已保存");
      } else {
        await createTermMapping(payload);
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑术语映射" : "新建术语映射"}</DialogTitle>
          <DialogDescription>查询改写时将原始词替换为目标词，优先级越小越靠前。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="tm-source">原始词</Label>
            <Input id="tm-source" value={sourceTerm} onChange={(e) => setSourceTerm(e.target.value)} placeholder="例如：AI 智能体" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="tm-target">目标词</Label>
            <Input id="tm-target" value={targetTerm} onChange={(e) => setTargetTerm(e.target.value)} placeholder="例如：Agent" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="tm-priority">优先级</Label>
              <Input id="tm-priority" type="number" value={priority} onChange={(e) => setPriority(e.target.value)} />
            </div>
            <div className="flex items-end gap-2 pb-1">
              <Checkbox id="tm-enabled" checked={enabled} onCheckedChange={(v) => setEnabled(v === true)} />
              <Label htmlFor="tm-enabled" className="cursor-pointer">
                启用
              </Label>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="tm-remark">备注</Label>
            <Input id="tm-remark" value={remark} onChange={(e) => setRemark(e.target.value)} placeholder="可选" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !sourceTerm.trim() || !targetTerm.trim()}>
            {submitting ? "保存中…" : isEdit ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function TermMappingPage() {
  const [records, setRecords] = useState<TermMapping[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [name, setName] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<TermMapping | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TermMapping | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getTermMappingsPage(current, PAGE_SIZE, keyword || undefined);
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

  const doDelete = async (m: TermMapping) => {
    if (!m.id) return;
    try {
      await deleteTermMapping(m.id);
      toast.success("已删除");
      setDeleteTarget(null);
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">术语映射</h1>
          <p className="text-sm text-muted-foreground">查询改写时把原始词归一为目标词，提升检索命中</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              className="w-56 pl-8"
              placeholder="搜索原始词/目标词"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSearch()}
            />
          </div>
          <Button variant="outline" onClick={onSearch}>
            搜索
          </Button>
          <Button
            onClick={() => {
              setEditTarget(null);
              setDialogOpen(true);
            }}
          >
            <Plus />
            新建映射
          </Button>
        </div>
      </div>

      {loading ? (
        <Loading label="加载术语映射…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无术语映射" description="点击「新建映射」添加第一条改写规则" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>原始词 → 目标词</TableHead>
                <TableHead>优先级</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((m) => (
                <TableRow key={m.id}>
                  <TableCell className="font-medium">
                    <span className="flex items-center gap-1.5">
                      <span>{m.sourceTerm || "-"}</span>
                      <ArrowRight className="text-muted-foreground size-3.5" />
                      <span>{m.targetTerm || "-"}</span>
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{m.priority ?? 0}</TableCell>
                  <TableCell>
                    {m.enabled ? <Badge>启用</Badge> : <Badge variant="secondary">停用</Badge>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDateTime(m.updateTime ?? m.createTime)}</TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                        <MoreHorizontal />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() => {
                            setEditTarget(m);
                            setDialogOpen(true);
                          }}
                        >
                          <Pencil />
                          编辑
                        </DropdownMenuItem>
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(m)}>
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

      {dialogOpen && (
        <MappingDialog
          target={editTarget}
          open
          onOpenChange={setDialogOpen}
          onSaved={() => {
            if (!editTarget) setCurrent(1);
            void load();
          }}
        />
      )}

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除术语映射</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「{deleteTarget?.sourceTerm || ""} → {deleteTarget?.targetTerm || ""}」吗？该操作不可恢复。
            </AlertDialogDescription>
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
