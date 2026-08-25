// M2 K1-K5 知识库列表页：分页/搜索/创建/重命名/删除（删除二次确认）
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Database, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
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
import { getSystemSettings, type ModelCandidate } from "@/shared/api/settings";

import { createKnowledgeBase, deleteKnowledgeBase, getKnowledgeBasesPage, updateKnowledgeBase } from "../api";
import { formatDateTime } from "../format";
import type { KnowledgeBase } from "../types";

const PAGE_SIZE = 10;

function CreateDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [collectionName, setCollectionName] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState<string | undefined>(undefined);
  const [candidates, setCandidates] = useState<ModelCandidate[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // 打开时（条件渲染即挂载）拉取 Embedding 模型候选（异步回调，不触发同步 setState）
  useEffect(() => {
    void getSystemSettings()
      .then((s) => setCandidates(s.ai?.embedding?.candidates ?? []))
      .catch(() => setCandidates([]));
  }, []);

  const submit = async () => {
    if (!name.trim() || !collectionName.trim()) return;
    setSubmitting(true);
    try {
      await createKnowledgeBase({
        name: name.trim(),
        collectionName: collectionName.trim(),
        embeddingModel: embeddingModel || null,
      });
      toast.success("知识库创建成功");
      onOpenChange(false);
      onCreated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>创建知识库</DialogTitle>
          <DialogDescription>填写名称与向量集合名，可选指定 Embedding 模型。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="kb-name">名称</Label>
            <Input id="kb-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：产品资料库" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="kb-collection">向量集合名</Label>
            <Input id="kb-collection" value={collectionName} onChange={(e) => setCollectionName(e.target.value)} placeholder="例如：product_docs" />
          </div>
          <div className="grid gap-1.5">
            <Label>Embedding 模型</Label>
            <Select value={embeddingModel ?? ""} onValueChange={(v) => setEmbeddingModel(v || undefined)}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="默认（不指定）" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">默认（不指定）</SelectItem>
                {candidates.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.model ?? c.id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !name.trim() || !collectionName.trim()}>
            {submitting ? "创建中…" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RenameDialog({ kb, open, onOpenChange, onRenamed }: { kb: KnowledgeBase | null; open: boolean; onOpenChange: (v: boolean) => void; onRenamed: () => void }) {
  // 条件渲染即挂载，初始值取自目标 kb，无需 effect 同步
  const [name, setName] = useState(kb?.name ?? "");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!kb || !name.trim() || name.trim() === kb.name) return;
    setSubmitting(true);
    try {
      await updateKnowledgeBase(kb.id, { name: name.trim() });
      toast.success("已重命名");
      onOpenChange(false);
      onRenamed();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "重命名失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重命名知识库</DialogTitle>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor="kb-rename">名称</Label>
          <Input id="kb-rename" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !name.trim() || name.trim() === kb?.name}>
            {submitting ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function KnowledgeListPage() {
  const navigate = useNavigate();
  const [records, setRecords] = useState<KnowledgeBase[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [name, setName] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<KnowledgeBase | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeBase | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getKnowledgeBasesPage(current, PAGE_SIZE, keyword || undefined);
      setRecords(page.records);
      setTotal(page.total);
      setPages(page.pages);
      // 当前页越界时回退到最后一页
      if (page.pages > 0 && current > page.pages) {
        setCurrent(page.pages);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [current, keyword]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  const onSearch = () => {
    setCurrent(1);
    setKeyword(name.trim());
  };

  const doDelete = async (kb: KnowledgeBase) => {
    try {
      await deleteKnowledgeBase(kb.id);
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
          <h1 className="text-lg font-semibold">知识库</h1>
          <p className="text-sm text-muted-foreground">管理向量知识库，文档与分块</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              className="w-56 pl-8"
              placeholder="搜索名称"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSearch()}
            />
          </div>
          <Button variant="outline" onClick={onSearch}>
            搜索
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus />
            新建知识库
          </Button>
        </div>
      </div>

      {loading ? (
        <Loading label="加载知识库…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无知识库" description="点击「新建知识库」创建第一个向量库" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>向量集合</TableHead>
                <TableHead>Embedding</TableHead>
                <TableHead>文档数</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((kb) => (
                <TableRow
                  key={kb.id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/admin/knowledge/${kb.id}/documents`)}
                >
                  <TableCell className="font-medium">
                    <span className="flex items-center gap-2">
                      <Database className="text-muted-foreground size-4" />
                      {kb.name}
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{kb.collectionName}</TableCell>
                  <TableCell>
                    {kb.embeddingModel ? <Badge variant="secondary">{kb.embeddingModel}</Badge> : <span className="text-muted-foreground">-</span>}
                  </TableCell>
                  <TableCell>{kb.documentCount ?? 0}</TableCell>
                  <TableCell className="text-muted-foreground">{formatDateTime(kb.updateTime ?? kb.createTime)}</TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                        <MoreHorizontal />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => setRenameTarget(kb)}>
                          <Pencil />
                          重命名
                        </DropdownMenuItem>
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(kb)}>
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

      {createOpen && (
        <CreateDialog open onOpenChange={setCreateOpen} onCreated={() => { setCurrent(1); void load(); }} />
      )}

      {renameTarget && (
        <RenameDialog
          kb={renameTarget}
          open={Boolean(renameTarget)}
          onOpenChange={(v) => {
            if (!v) setRenameTarget(null);
          }}
          onRenamed={() => void load()}
        />
      )}

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除知识库</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「{deleteTarget?.name}」吗？该操作不可恢复；若库内存在未删除文档将被拒绝。
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
