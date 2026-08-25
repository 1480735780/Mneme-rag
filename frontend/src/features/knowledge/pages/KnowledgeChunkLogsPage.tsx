// M2 D10 分块日志页：分页展示每次分块运行（各阶段耗时/分块数/错误）
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Clock } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";

import { getChunkLogsPage, getDocument } from "../api";
import { formatDateTime } from "../format";
import { documentStatusMeta } from "../status";
import type { KnowledgeDocumentChunkLog } from "../types";

const PAGE_SIZE = 10;

function formatDuration(v?: number | null): string {
  if (v === null || v === undefined) return "-";
  return `${v}ms`;
}

export default function KnowledgeChunkLogsPage() {
  const { kbId = "", docId = "" } = useParams();
  const [docName, setDocName] = useState("");
  const [records, setRecords] = useState<KnowledgeDocumentChunkLog[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getChunkLogsPage(docId, current, PAGE_SIZE);
      setRecords(page.records);
      setTotal(page.total);
      setPages(page.pages);
      if (page.pages > 0 && current > page.pages) setCurrent(page.pages);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [docId, current]);

  useEffect(() => {
    void getDocument(docId)
      .then((d) => setDocName(d.docName))
      .catch(() => setDocName(""));
  }, [docId]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon-sm" nativeButton={false} render={<Link to={`/admin/knowledge/${kbId}/documents/${docId}/chunks`} />} aria-label="返回分块列表">
          <ArrowLeft />
        </Button>
        <div>
          <h1 className="text-lg font-semibold">{docName || "分块日志"}</h1>
          <p className="text-sm text-muted-foreground">每次分块运行的状态与阶段耗时</p>
        </div>
      </div>

      {loading ? (
        <Loading label="加载日志…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无分块日志" description="文档开始分块后生成运行记录" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>状态</TableHead>
                <TableHead>模式</TableHead>
                <TableHead>解析档位</TableHead>
                <TableHead>分块数</TableHead>
                <TableHead>耗时</TableHead>
                <TableHead>开始时间</TableHead>
                <TableHead>结束时间</TableHead>
                <TableHead>错误信息</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((log) => {
                const meta = documentStatusMeta(log.status);
                return (
                  <TableRow key={log.id}>
                    <TableCell>
                      <Badge variant={meta.tone}>{meta.label}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{log.processMode ?? "-"}</TableCell>
                    <TableCell className="text-muted-foreground">{log.parseProfile ?? "-"}</TableCell>
                    <TableCell>{log.chunkCount ?? "-"}</TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1 text-muted-foreground" title={`提取 ${formatDuration(log.extractDuration)} / 分块 ${formatDuration(log.chunkDuration)} / 向量 ${formatDuration(log.embedDuration)} / 落库 ${formatDuration(log.persistDuration)}`}>
                        <Clock className="size-3.5" />
                        {formatDuration(log.totalDuration)}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(log.startTime)}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(log.endTime)}</TableCell>
                    <TableCell className="max-w-48">
                      {log.errorMessage ? (
                        <span className="block truncate text-destructive" title={log.errorMessage}>
                          {log.errorMessage}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
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
    </div>
  );
}
