// M2 D2 上传文档弹窗：file/url 来源 + chunk/pipeline 模式 + 动态摄取 schema（档位/预算字段）
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { getIngestionSpecSchema, uploadDocument } from "../api";
import type { BudgetFieldSchema, IngestionSpecSchema } from "../types";

interface UploadDocumentDialogProps {
  kbId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onUploaded: () => void;
}

/** 上传可接受扩展名（对齐解析器注册表 + MinerU 支持清单） */
const ACCEPT = ".md,.txt,.csv,.xls,.xlsx,.png,.jpg,.jpeg,.svg,.pdf,.doc,.docx,.ppt,.pptx";

/** 上传大小上限（对齐后端默认 maxFileSize 50MB，前端提前拦截） */
const MAX_FILE_SIZE = 50 * 1024 * 1024;

function extOf(fileName?: string): string {
  if (!fileName) return "";
  const idx = fileName.lastIndexOf(".");
  return idx < 0 ? "" : fileName.slice(idx + 1).toLowerCase();
}

export default function UploadDocumentDialog({ kbId, open, onOpenChange, onUploaded }: UploadDocumentDialogProps) {
  const [sourceType, setSourceType] = useState<"file" | "url">("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [processMode, setProcessMode] = useState<"chunk" | "pipeline">("chunk");
  const [pipelineId, setPipelineId] = useState("");
  const [parseProfile, setParseProfile] = useState<string | undefined>(undefined);
  const [budgets, setBudgets] = useState<Record<string, number>>({});
  const [schema, setSchema] = useState<IngestionSpecSchema | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 打开时（条件渲染即挂载）拉取摄取配置 schema（异步回调，不触发同步 setState）
  useEffect(() => {
    void getIngestionSpecSchema()
      .then((s) => {
        setSchema(s);
        setParseProfile(s.parseProfiles[0]?.value);
      })
      .catch(() => {
        setSchema(null);
        setParseProfile(undefined);
        toast.error("加载摄取配置失败");
      });
  }, []);

  const ext = useMemo(() => extOf(file?.name), [file]);
  // 档位仅对「两档命中不同解析器」的格式展示（后端下发 parseProfileExtensions）
  const profileRelevant = useMemo(() => Boolean(schema && ext && schema.parseProfileExtensions.includes(ext)), [schema, ext]);

  const specJson = useMemo(() => {
    if (processMode !== "chunk" || !schema) return undefined;
    const map: Record<string, unknown> = {};
    if (profileRelevant && parseProfile) map.parseProfile = parseProfile;
    for (const f of schema.budgetFields) {
      const v = budgets[f.key];
      map[f.key] = v === undefined ? f.defaultValue : v;
    }
    return JSON.stringify(map);
  }, [processMode, schema, profileRelevant, parseProfile, budgets]);

  const setBudget = (key: string, raw: string, field: BudgetFieldSchema) => {
    const n = Number(raw);
    if (raw === "" || Number.isNaN(n)) {
      setBudgets((prev) => ({ ...prev, [key]: undefined as unknown as number }));
      return;
    }
    const clamped = Math.min(Math.max(n, field.min), field.max);
    setBudgets((prev) => ({ ...prev, [key]: clamped }));
  };

  const submit = async () => {
    if (sourceType === "file" && !file) {
      toast.error("请选择要上传的文件");
      return;
    }
    if (sourceType === "url" && !url.trim()) {
      toast.error("请输入来源 URL");
      return;
    }
    setSubmitting(true);
    try {
      await uploadDocument(kbId, {
        sourceType,
        file: sourceType === "file" ? file : null,
        sourceLocation: sourceType === "url" ? url.trim() : undefined,
        processMode,
        pipelineId: processMode === "pipeline" ? pipelineId || undefined : undefined,
        ingestionSpec: specJson,
      });
      toast.success("上传成功，开始处理");
      onOpenChange(false);
      onUploaded();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "上传失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>上传文档</DialogTitle>
          <DialogDescription>支持文件或 URL 来源；表格类文件可配置解析档位与分块预算。</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          {/* 来源类型 */}
          <div className="grid grid-cols-2 gap-2">
            {(["file", "url"] as const).map((t) => (
              <Button
                key={t}
                type="button"
                variant={sourceType === t ? "default" : "outline"}
                onClick={() => setSourceType(t)}
              >
                {t === "file" ? "本地上传" : "URL 导入"}
              </Button>
            ))}
          </div>

          {sourceType === "file" ? (
            <div className="grid gap-1.5">
              <Label htmlFor="doc-file">文件</Label>
              <Input
                id="doc-file"
                type="file"
                accept={ACCEPT}
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  if (f && f.size > MAX_FILE_SIZE) {
                    setFile(null);
                    toast.error("文件超过 50MB 大小上限");
                    e.target.value = "";
                    return;
                  }
                  setFile(f);
                }}
              />
              {file && <p className="text-xs text-muted-foreground">已选择：{file.name}</p>}
            </div>
          ) : (
            <div className="grid gap-1.5">
              <Label htmlFor="doc-url">来源 URL</Label>
              <Input id="doc-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/doc.md" />
            </div>
          )}

          {/* 处理模式 */}
          <div className="grid gap-1.5">
            <Label>处理模式</Label>
            <Select value={processMode} onValueChange={(v) => setProcessMode(v as "chunk" | "pipeline")}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="chunk">分块入库（chunk）</SelectItem>
                <SelectItem value="pipeline">摄取流水线（pipeline）</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {processMode === "pipeline" ? (
            <div className="grid gap-1.5">
              <Label htmlFor="doc-pipeline">流水线 ID</Label>
              <Input id="doc-pipeline" value={pipelineId} onChange={(e) => setPipelineId(e.target.value)} placeholder="pipeline-xxx" />
            </div>
          ) : (
            schema && (
              <div className="grid gap-3 rounded-lg border p-3">
                {profileRelevant && (
                  <div className="grid gap-1.5">
                    <Label>{schema.parseProfileLabel}</Label>
                    <Select value={parseProfile ?? ""} onValueChange={(v) => setParseProfile(v ?? undefined)}>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="请选择" />
                      </SelectTrigger>
                      <SelectContent>
                        {schema.parseProfiles.map((p) => (
                          <SelectItem key={p.value} value={p.value}>
                            {p.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3">
                  {schema.budgetFields.map((f) => (
                    <div key={f.key} className="grid gap-1.5">
                      <Tooltip>
                        <TooltipTrigger render={<Label className="w-fit">{f.label}</Label>} />
                        <TooltipContent>{f.detail ?? f.hint}</TooltipContent>
                      </Tooltip>
                      <Input
                        type="number"
                        defaultValue={f.defaultValue}
                        min={f.min}
                        max={f.max}
                        onChange={(e) => setBudget(f.key, e.target.value, f)}
                      />
                    </div>
                  ))}
                </div>
              </div>
            )
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? "上传中…" : "上传"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
