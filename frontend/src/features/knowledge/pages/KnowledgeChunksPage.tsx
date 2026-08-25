// M2 C1-C6 Chunk 列表页：分页/启停过滤 + CRUD + 单条启停 + 批量启停（勾选二次确认）
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, FileText, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";

import {
  batchToggleChunks,
  createChunk,
  deleteChunk,
  getChunksPage,
  getDocument,
  toggleChunk,
  updateChunk,
} from "../api";
import { formatDateTime } from "../format";
import { chunkEnabledMeta } from "../status";
import type { KnowledgeChunk } from "../types";

const PAGE_SIZE = 10;
/** 当前页勾选的 chunk id */
type Selection = Record<string, boolean>;

interface EditDialogProps {
  chunk: KnowledgeChunk | null;
  isCreate: boolean;
  docId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}

function EditDialog({ chunk, isCreate, docId, open, onOpenChange, onSaved }: EditDialogProps) {
  // 条件渲染即挂载，初始值取自目标 chunk，无需 effect 同步
  const [content, setContent] = useState(chunk?.content ?? "");
  const [index, setIndex] = useState(
    chunk?.chunkIndex !== undefined && chunk.chunkIndex !== null ? String(chunk.chunkIndex) : "",
  );
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!content.trim()) return;
    setSubmitting(true);
    try {
      if (isCreate) {
        await createChunk(docId, {
          content: content.trim(),
          index: index === "" ? null : Number(index),
        });
        toast.success("已新增 Chunk");
      } else if (chunk) {
        await updateChunk(docId, chunk.id, { content: content.trim() });
        toast.success("已更新 Chunk");
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
          <DialogTitle>{isCreate ? "新增 Chunk" : "编辑 Chunk"}</DialogTitle>
          <DialogDescription>
            {isCreate ? "手动新增一条分块内容，将重新入向量索引。" : "修改内容后将重新入向量索引。"}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          {isCreate && (
            <div className="grid gap-1.5">
              <Label htmlFor="chunk-index">序号（可选）</Label>
              <Input id="chunk-index" type="number" value={index} onChange={(e) => setIndex(e.target.value)} placeholder="留空自动排序" />
            </div>
          )}
          <div className="grid gap-1.5">
            <Label htmlFor="chunk-content">内容</Label>
            <Textarea id="chunk-content" className="min-h-40" value={content} onChange={(e) => setContent(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !content.trim()}>
            {submitting ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function KnowledgeChunksPage() {
  const { kbId = "", docId = "" } = useParams();
  const [docName, setDocName] = useState("");
  const [records, setRecords] = useState<KnowledgeChunk[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [enabled, setEnabled] = useState<number | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [editTarget, setEditTarget] = useState<{ chunk: KnowledgeChunk | null; isCreate: boolean } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeChunk | null>(null);
  const [batchAction, setBatchAction] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getChunksPage(docId, { current, size: PAGE_SIZE, enabled });
      setRecords(page.records);
      setTotal(page.total);
      setPages(page.pages);
      if (page.pages > 0 && current > page.pages) setCurrent(page.pages);
      setSelection({});
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [docId, current, enabled]);

  useEffect(() => {
    void getDocument(docId)
      .then((d) => setDocName(d.docName))
      .catch(() => setDocName(""));
  }, [docId]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  const selectedIds = records.filter((c) => selection[c.id]).map((c) => c.id);

  const toggleSelection = (id: string) => {
    setSelection((prev) => ({ ...prev, [id]: !prev[id] }));
  };
  const toggleAll = () => {
    const allSelected = records.length > 0 && selectedIds.length === records.length;
    setSelection(Object.fromEntries(records.map((c) => [c.id, !allSelected])));
  };

  const doToggleChunk = async (chunk: KnowledgeChunk) => {
    const next = chunk.enabled === 1 ? false : true;
    try {
      await toggleChunk(docId, chunk.id, next);
      toast.success(next ? "已启用" : "已禁用");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const doBatchToggle = async (value: boolean) => {
    if (selectedIds.length === 0) return;
    try {
      await batchToggleChunks(docId, value, selectedIds);
      toast.success(value ? `已启用 ${selectedIds.length} 条` : `已禁用 ${selectedIds.length} 条`);
      setBatchAction(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const doDelete = async (chunk: KnowledgeChunk) => {
    try {
      await deleteChunk(docId, chunk.id);
      toast.success("已删除");
      setDeleteTarget(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon-sm" nativeButton={false} render={<Link to={`/admin/knowledge/${kbId}/documents`} />} aria-label="返回文档列表">
            <ArrowLeft />
          </Button>
          <div>
            <h1 className="text-lg font-semibold">{docName || "分块管理"}</h1>
            <p className="text-sm text-muted-foreground">查看与编辑文档分块，可单条或批量启停</p>
          </div>
        </div>
        <Button onClick={() => setEditTarget({ chunk: null, isCreate: true })}>
          <Plus />
          新增 Chunk
        </Button>
      </div>

      {/* 过滤 + 批量操作条 */}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={enabled === undefined ? "" : String(enabled)}
          onValueChange={(v) => {
            setEnabled(v === "" ? undefined : Number(v));
            setCurrent(1);
          }}
        >
          <SelectTrigger className="w-32">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">全部状态</SelectItem>
            <SelectItem value="1">启用</SelectItem>
            <SelectItem value="0">禁用</SelectItem>
          </SelectContent>
        </Select>
        {selectedIds.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">已选 {selectedIds.length} 条</span>
            <Button size="sm" onClick={() => setBatchAction(true)}>
              启用所选
            </Button>
            <Button size="sm" variant="outline" onClick={() => setBatchAction(false)}>
              禁用所选
            </Button>
          </div>
        )}
      </div>

      {loading ? (
        <Loading label="加载分块…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无分块" description="文档处理完成后自动生成，或点击「新增 Chunk」手动添加" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <Checkbox
                    aria-label="全选"
                    checked={selectedIds.length === records.length && records.length > 0}
                    onCheckedChange={toggleAll}
                  />
                </TableHead>
                <TableHead className="w-14">序号</TableHead>
                <TableHead>内容</TableHead>
                <TableHead>Hash</TableHead>
                <TableHead>字数</TableHead>
                <TableHead>Token</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((chunk) => {
                const meta = chunkEnabledMeta(chunk.enabled);
                return (
                  <TableRow key={chunk.id}>
                    <TableCell>
                      <Checkbox
                        aria-label={`选择分块 ${chunk.chunkIndex ?? ""}`}
                        checked={Boolean(selection[chunk.id])}
                        onCheckedChange={() => toggleSelection(chunk.id)}
                      />
                    </TableCell>
                    <TableCell className="text-muted-foreground">{chunk.chunkIndex ?? "-"}</TableCell>
                    <TableCell className="max-w-md">
                      <span className="line-clamp-2 block whitespace-normal text-foreground/90">{chunk.content || "-"}</span>
                    </TableCell>
                    <TableCell>
                      <span className="text-muted-foreground" title={chunk.contentHash ?? ""}>
                        {(chunk.contentHash ?? "-").slice(0, 8)}
                      </span>
                    </TableCell>
                    <TableCell>{chunk.charCount ?? 0}</TableCell>
                    <TableCell>{chunk.tokenCount ?? 0}</TableCell>
                    <TableCell>
                      <Badge variant={meta.tone}>{meta.label}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(chunk.updateTime ?? chunk.createTime)}</TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button variant="ghost" size="icon-sm" aria-label="编辑" onClick={() => setEditTarget({ chunk, isCreate: false })}>
                          <Pencil />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={chunk.enabled === 1 ? "禁用" : "启用"}
                          onClick={() => void doToggleChunk(chunk)}
                        >
                          <FileText />
                        </Button>
                        <Button variant="ghost" size="icon-sm" aria-label="删除" onClick={() => setDeleteTarget(chunk)}>
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
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

      {editTarget && (
        <EditDialog
          docId={docId}
          chunk={editTarget.chunk}
          isCreate={editTarget.isCreate}
          open={Boolean(editTarget)}
          onOpenChange={(v) => {
            if (!v) setEditTarget(null);
          }}
          onSaved={() => void load()}
        />
      )}

      {/* 单条删除确认 */}
      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除 Chunk</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除序号 {deleteTarget?.chunkIndex ?? "-"} 的分块吗？将同时从向量索引移除。
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

      {/* 批量启停确认 */}
      <AlertDialog open={batchAction !== null} onOpenChange={(v) => { if (!v) setBatchAction(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{batchAction ? "启用所选" : "禁用所选"}</AlertDialogTitle>
            <AlertDialogDescription>
              确定{batchAction ? "启用" : "禁用"}已选的 {selectedIds.length} 条分块吗？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => batchAction !== null && void doBatchToggle(batchAction)}>
              {batchAction ? "启用" : "禁用"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
