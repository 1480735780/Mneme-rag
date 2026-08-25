// M2 D1-D12 文档列表页：分页/关键字/状态过滤 + 上传（动态 schema）+ 分块/启停/预览/下载/删除
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, FileText, HardDriveDownload, MoreHorizontal, Pencil, Play, ScrollText, Search, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";

import { deleteDocument, downloadDocumentFile, enableDocument, getDocumentsPage, getKnowledgeBase, startDocumentChunk } from "../api";
import { formatDateTime, formatFileSize } from "../format";
import { documentStatusMeta } from "../status";
import type { KnowledgeDocument } from "../types";
import UploadDocumentDialog from "../components/UploadDocumentDialog";

const PAGE_SIZE = 10;
/** 处理中状态（需轮询刷新） */
const ACTIVE_STATUS = new Set(["pending", "running"]);

export default function KnowledgeDocumentsPage() {
  const { kbId = "" } = useParams();
  const navigate = useNavigate();
  const [kbName, setKbName] = useState<string>("");
  const [records, setRecords] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [status, setStatus] = useState<string>("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocument | null>(null);
  const [busyDocId, setBusyDocId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      setError(null);
      try {
        const page = await getDocumentsPage(kbId, { current, size: PAGE_SIZE, status: status || undefined, keyword: keyword || undefined });
        setRecords(page.records);
        setTotal(page.total);
        setPages(page.pages);
        if (page.pages > 0 && current > page.pages) setCurrent(page.pages);
        return page.records;
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载失败");
        return [] as KnowledgeDocument[];
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [kbId, current, status, keyword],
  );

  // 库名（页面标题）
  useEffect(() => {
    void getKnowledgeBase(kbId)
      .then((kb) => setKbName(kb.name))
      .catch(() => setKbName(""));
  }, [kbId]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  // 处理中状态轮询（3s 指数退避，直到无 running/pending）
  useEffect(() => {
    const hasActive = records.some((d) => ACTIVE_STATUS.has(d.status ?? ""));
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (hasActive) {
      pollRef.current = setInterval(() => void load(true), 3000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [records, load]);

  const onSearch = () => {
    setCurrent(1);
    void load();
  };

  const doStartChunk = async (doc: KnowledgeDocument) => {
    setBusyDocId(doc.id);
    try {
      await startDocumentChunk(doc.id);
      toast.success("已开始分块");
      await load(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "分块失败");
    } finally {
      setBusyDocId(null);
    }
  };

  const doToggleEnable = async (doc: KnowledgeDocument) => {
    try {
      await enableDocument(doc.id, !doc.enabled);
      toast.success(doc.enabled ? "已禁用" : "已启用");
      await load(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "操作失败");
    }
  };

  const doDelete = async (doc: KnowledgeDocument) => {
    try {
      await deleteDocument(doc.id);
      toast.success("已删除");
      setDeleteTarget(null);
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  const doDownload = async (doc: KnowledgeDocument) => {
    try {
      const blob = await downloadDocumentFile(doc.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = doc.docName || "document";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "下载失败");
    }
  };

  const isRunning = (d: KnowledgeDocument) => d.status === "running";

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon-sm" nativeButton={false} render={<Link to="/admin/knowledge" />} aria-label="返回知识库列表">
            <ArrowLeft />
          </Button>
          <div>
            <h1 className="text-lg font-semibold">{kbName || "文档管理"}</h1>
            <p className="text-sm text-muted-foreground">文档上传、解析状态与分块入口</p>
          </div>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <Upload />
          上传文档
        </Button>
      </div>

      {/* 过滤条 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            className="w-56 pl-8"
            placeholder="搜索文档名"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSearch()}
          />
        </div>
        <Select value={status} onValueChange={(v) => { setStatus(v ?? ""); setCurrent(1); }}>
          <SelectTrigger className="w-32">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">全部状态</SelectItem>
            <SelectItem value="pending">待处理</SelectItem>
            <SelectItem value="running">处理中</SelectItem>
            <SelectItem value="success">成功</SelectItem>
            <SelectItem value="failed">失败</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={onSearch}>
          搜索
        </Button>
      </div>

      {loading ? (
        <Loading label="加载文档…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无文档" description="点击「上传文档」添加文件或 URL 来源" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>文档名</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>分块数</TableHead>
                <TableHead>大小</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((doc) => {
                const meta = documentStatusMeta(doc.status);
                return (
                  <TableRow key={doc.id} className="cursor-pointer" onClick={() => navigateToChunks(doc)}>
                    <TableCell className="font-medium">
                      <span className="flex items-center gap-2">
                        <FileText className="text-muted-foreground size-4" />
                        <span className="max-w-64 truncate">{doc.docName}</span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant={meta.tone}>{meta.label}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{doc.sourceType === "url" ? "URL" : doc.fileType ?? "file"}</TableCell>
                    <TableCell>{doc.chunkCount ?? 0}</TableCell>
                    <TableCell className="text-muted-foreground">{formatFileSize(doc.fileSize)}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(doc.updateTime ?? doc.createTime)}</TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <DropdownMenu>
                        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                          <MoreHorizontal />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem disabled={isRunning(doc)} onClick={() => void doStartChunk(doc)}>
                            <Play />
                            {busyDocId === doc.id ? "分块中…" : "开始分块"}
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => void doToggleEnable(doc)}>
                            {doc.enabled ? "禁用" : "启用"}
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => navigateToPreview(doc)}>
                            <ScrollText />
                            预览
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => navigateToLogs(doc)}>
                            <Pencil />
                            分块日志
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => void doDownload(doc)}>
                            <HardDriveDownload />
                            下载
                          </DropdownMenuItem>
                          <DropdownMenuItem variant="destructive" disabled={isRunning(doc)} onClick={() => setDeleteTarget(doc)}>
                            <Trash2 />
                            删除
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
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

      {uploadOpen && (
        <UploadDocumentDialog kbId={kbId} open onOpenChange={setUploadOpen} onUploaded={() => void load()} />
      )}

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除文档</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「{deleteTarget?.docName}」吗？处理中的文档不可删除；删除后其分块与向量一并移除。
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

  function navigateToChunks(doc: KnowledgeDocument) {
    navigate(`/admin/knowledge/${kbId}/documents/${doc.id}/chunks`);
  }
  function navigateToPreview(doc: KnowledgeDocument) {
    navigate(`/admin/knowledge/${kbId}/documents/${doc.id}/preview`);
  }
  function navigateToLogs(doc: KnowledgeDocument) {
    navigate(`/admin/knowledge/${kbId}/documents/${doc.id}/logs`);
  }
}
