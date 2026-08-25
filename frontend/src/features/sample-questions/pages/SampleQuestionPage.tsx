// M4B T4 示例问题管理页：分页/搜索/创建/编辑/删除（二次确认）
import { useCallback, useEffect, useState } from "react";
import { MessageCircleQuestion, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime } from "@/shared/format";

import { createSampleQuestion, deleteSampleQuestion, getSampleQuestionsPage, updateSampleQuestion } from "../api";
import type { SampleQuestion } from "../types";

const PAGE_SIZE = 10;

function QuestionDialog({
  target,
  open,
  onOpenChange,
  onSaved,
}: {
  target: SampleQuestion | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(target?.title ?? "");
  const [description, setDescription] = useState(target?.description ?? "");
  const [question, setQuestion] = useState(target?.question ?? "");
  const [submitting, setSubmitting] = useState(false);
  const isEdit = Boolean(target);

  const submit = async () => {
    if (!question.trim()) return;
    setSubmitting(true);
    try {
      const payload = {
        title: title.trim() || undefined,
        description: description.trim() || undefined,
        question: question.trim(),
      };
      if (target?.id) {
        await updateSampleQuestion(target.id, payload);
        toast.success("已保存");
      } else {
        await createSampleQuestion(payload);
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
          <DialogTitle>{isEdit ? "编辑示例问题" : "新建示例问题"}</DialogTitle>
          <DialogDescription>展示在聊天欢迎页的推荐问题，question 必填。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="sq-title">标题</Label>
            <Input id="sq-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="可选" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="sq-question">问题</Label>
            <Textarea id="sq-question" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="例如：什么是 RAG？" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="sq-desc">描述</Label>
            <Textarea id="sq-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !question.trim()}>
            {submitting ? "保存中…" : isEdit ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function SampleQuestionPage() {
  const [records, setRecords] = useState<SampleQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [name, setName] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<SampleQuestion | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SampleQuestion | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getSampleQuestionsPage(current, PAGE_SIZE, keyword || undefined);
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

  const doDelete = async (q: SampleQuestion) => {
    if (!q.id) return;
    try {
      await deleteSampleQuestion(q.id);
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
          <h1 className="text-lg font-semibold">示例问题</h1>
          <p className="text-sm text-muted-foreground">维护聊天欢迎页展示的推荐问题</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              className="w-56 pl-8"
              placeholder="搜索问题"
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
            新建问题
          </Button>
        </div>
      </div>

      {loading ? (
        <Loading label="加载示例问题…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无示例问题" description="点击「新建问题」添加第一条推荐问题" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>标题</TableHead>
                <TableHead>问题</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((q) => (
                <TableRow key={q.id}>
                  <TableCell className="font-medium">
                    <span className="flex items-center gap-2">
                      <MessageCircleQuestion className="text-muted-foreground size-4" />
                      {q.title || "-"}
                    </span>
                  </TableCell>
                  <TableCell className="max-w-96">
                    <span className="block truncate text-muted-foreground">{q.question || "-"}</span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDateTime(q.updateTime ?? q.createTime)}</TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                        <MoreHorizontal />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() => {
                            setEditTarget(q);
                            setDialogOpen(true);
                          }}
                        >
                          <Pencil />
                          编辑
                        </DropdownMenuItem>
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(q)}>
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
        <QuestionDialog
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
            <AlertDialogTitle>删除示例问题</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「{deleteTarget?.question || deleteTarget?.title || ""}」吗？该操作不可恢复。
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
