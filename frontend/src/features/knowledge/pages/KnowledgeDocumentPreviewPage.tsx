// M2 D11/D12 文档预览页：markdown 安全渲染 + 源文件下载
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, HardDriveDownload } from "lucide-react";
import { toast } from "sonner";

import { Markdown } from "@/features/chat/components/Markdown";
import { Button } from "@/components/ui/button";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";

import { downloadDocumentFile, getDocument, previewDocument } from "../api";

export default function KnowledgeDocumentPreviewPage() {
  const { kbId = "", docId = "" } = useParams();
  const [docName, setDocName] = useState("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [doc, text] = await Promise.all([getDocument(docId), previewDocument(docId)]);
      setDocName(doc.docName);
      setContent(text);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  const doDownload = async () => {
    try {
      const blob = await downloadDocumentFile(docId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = docName || "document";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "下载失败");
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon-sm" nativeButton={false} render={<Link to={`/admin/knowledge/${kbId}/documents/${docId}/chunks`} />} aria-label="返回分块列表">
            <ArrowLeft />
          </Button>
          <div>
            <h1 className="text-lg font-semibold">{docName || "文档预览"}</h1>
            <p className="text-sm text-muted-foreground">解析后的 Markdown 内容</p>
          </div>
        </div>
        <Button variant="outline" onClick={() => void doDownload()}>
          <HardDriveDownload />
          下载源文件
        </Button>
      </div>

      {loading ? (
        <Loading label="加载预览…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : !content.trim() ? (
        <Empty title="暂无内容" description="该文档解析后为空，或尚未完成解析" />
      ) : (
        <div className="min-w-0 max-w-3xl rounded-lg border bg-card p-6">
          <Markdown content={content} />
        </div>
      )}
    </div>
  );
}
