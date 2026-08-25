// M3 Settings 只读页：编排模式 / 上传限制 / RAG 配置（默认+开关+限流+记忆）/ AI 模型组
// M4A T3 追加：账号安全（修改当前用户密码）
// 全部为后端投影只读展示；apiKey 后端已脱敏，前端不渲染明文
import { useCallback, useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { toast } from "sonner";
import { useAuthStore } from "@/features/auth/store";
import { ROLE_ADMIN } from "@/features/auth/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatFileSize } from "@/shared/format";
import { getSystemSettings, type ModelGroup, type SystemSettings } from "@/shared/api/settings";
import { changePassword } from "@/features/users/api";

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium tabular-nums">{value || "-"}</span>
    </div>
  );
}

function Enabled({ enabled }: { enabled?: boolean | null }) {
  return enabled ? <Badge>启用</Badge> : <Badge variant="secondary">关闭</Badge>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border bg-card p-4">
      <h2 className="mb-2 text-sm font-semibold">{title}</h2>
      <div className="divide-y divide-border">{children}</div>
    </section>
  );
}

function modelGroupLabel(group?: ModelGroup | null): string {
  if (!group) return "未配置";
  const model = group.defaultModel ?? group.candidates?.[0]?.model;
  return model ? `${group.defaultTier ? `${group.defaultTier} · ` : ""}${model}` : "未配置";
}

function modelGroupDetail(group?: ModelGroup | null): string {
  if (!group || !group.candidates?.length) return "";
  return group.candidates
    .filter((c) => c.enabled !== false)
    .map((c) => c.model)
    .filter(Boolean)
    .join("、");
}

/** M4A T3：修改当前用户密码对话框（PUT /user/password，snake_case 请求体） */
function ChangePasswordDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!oldPassword || !newPassword) return;
    if (newPassword !== confirm) {
      toast.error("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      await changePassword({ old_password: oldPassword, new_password: newPassword });
      toast.success("密码已修改");
      onOpenChange(false);
      setOldPassword("");
      setNewPassword("");
      setConfirm("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "修改失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>修改密码</DialogTitle>
          <DialogDescription>验证旧密码后设置新密码。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="old-pass">旧密码</Label>
            <Input id="old-pass" type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-pass">新密码</Label>
            <Input id="new-pass" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="confirm-pass">确认新密码</Label>
            <Input id="confirm-pass" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !oldPassword || !newPassword || !confirm}>
            {submitting ? "提交中…" : "确认修改"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function SystemSettingsPage() {
  const user = useAuthStore((s) => s.user);
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [passOpen, setPassOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSettings(await getSystemSettings());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 微任务延迟，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    queueMicrotask(() => void load());
  }, [load]);

  if (user?.role !== ROLE_ADMIN) return null;

  const upload = settings?.upload;
  const rag = settings?.rag;
  const ai = settings?.ai;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div>
        <h1 className="text-lg font-semibold">系统设置</h1>
        <p className="text-sm text-muted-foreground">只读视图：编排模式、模型与检索配置（敏感信息已脱敏）</p>
      </div>

      {loading ? (
        <Loading label="加载设置…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : settings ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="编排与上传">
            <Field label="编排模式" value={settings.orchestrationMode ?? "-"} />
            <Field label="上传大小上限" value={formatFileSize(upload?.maxFileSize)} />
            <Field label="请求大小上限" value={formatFileSize(upload?.maxRequestSize)} />
          </Section>

          <Section title="RAG 默认检索">
            <Field label="默认集合" value={rag?.default?.collectionName ?? "-"} />
            <Field label="向量维度" value={String(rag?.default?.dimension ?? "-")} />
            <Field label="距离度量" value={rag?.default?.metricType ?? "-"} />
          </Section>

          <Section title="RAG 开关">
            <div className="flex items-center justify-between py-1.5">
              <span className="text-sm text-muted-foreground">Query 改写</span>
              <Enabled enabled={rag?.queryRewrite?.enabled} />
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-sm text-muted-foreground">来源引用</span>
              <Enabled enabled={rag?.citation?.enabled} />
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-sm text-muted-foreground">全局限流</span>
              {rag?.rateLimit?.global ? (
                <span className="text-sm text-muted-foreground">
                  {rag.rateLimit.global.enabled ? "启用" : "关闭"} · 并发 {rag.rateLimit.global.maxConcurrent ?? "-"}
                </span>
              ) : (
                <span className="text-sm text-muted-foreground">未启用</span>
              )}
            </div>
          </Section>

          <Section title="记忆">
            <Field label="历史保留轮数" value={String(rag?.memory?.historyKeepTurns ?? "-")} />
            <Field label="摘要" value={rag?.memory?.summaryEnabled ? "启用" : "关闭"} />
            <Field label="摘要起始轮数" value={String(rag?.memory?.summaryStartTurns ?? "-")} />
            <Field label="标题最大长度" value={String(rag?.memory?.titleMaxLength ?? "-")} />
          </Section>

          <Section title="对话模型">
            <Field label="默认模型" value={modelGroupLabel(ai?.chat)} />
            <Field label="深度思考档" value={ai?.chat?.deepThinkingTier ?? "-"} />
            <Field label="候选模型" value={modelGroupDetail(ai?.chat)} />
          </Section>

          <Section title="Embedding / Rerank">
            <Field label="Embedding" value={modelGroupLabel(ai?.embedding)} />
            <Field label="Rerank" value={modelGroupLabel(ai?.rerank)} />
            <Field label="流式块大小" value={String(ai?.stream?.messageChunkSize ?? "-")} />
          </Section>

          {ai?.providers && Object.keys(ai.providers).length > 0 && (
            <Section title="AI 提供商（已脱敏）">
              {Object.entries(ai.providers).map(([name, p]) => (
                <div key={name} className="py-1.5">
                  <div className="flex items-center justify-between gap-4">
                    <span className="text-sm font-medium">{name}</span>
                    <span className="font-mono text-xs text-muted-foreground">{p?.apiKey || "未配置"}</span>
                  </div>
                  {p?.url && <p className="mt-0.5 truncate text-xs text-muted-foreground">{p.url}</p>}
                </div>
              ))}
            </Section>
          )}

          <Section title="账号安全">
            <div className="flex items-center justify-between gap-4 py-1.5">
              <span className="text-sm text-muted-foreground">当前用户 {user?.username}</span>
              <Button variant="outline" size="sm" onClick={() => setPassOpen(true)}>
                <KeyRound />
                修改密码
              </Button>
            </div>
          </Section>
        </div>
      ) : null}

      {passOpen && <ChangePasswordDialog open onOpenChange={setPassOpen} />}
    </div>
  );
}
