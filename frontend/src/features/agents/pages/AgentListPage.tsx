// M4B T7 智能体档案页：列表/创建/编辑/激活/删除（二次确认）+ 槽位提示词编辑
// P10.2 T2.4：槽位版本管理（版本历史/diff 预览/激活与回滚——D8 保存≠生效、二次确认交互）
import { useCallback, useEffect, useState } from "react";
import { Bot, History, MoreHorizontal, Pencil, Play, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";

import {
  activateAgent,
  activateAgentPromptVersion,
  createAgent,
  deleteAgent,
  getAgentPrompts,
  getAgents,
  getDefaultAgentPrompt,
  listPromptVersions,
  previewPromptVersion,
  rollbackAgentPromptVersion,
  saveAgentPrompt,
  updateAgent,
} from "../api";
import type { AgentProfile, AgentPromptsView, PromptVersionItem, PromptVersionList, PromptVersionPreview } from "../types";

/** 生效内容来源（D8 读侧三态）的中文徽标 */
const CONTENT_SOURCE_LABEL: Record<string, string> = {
  pointer: "版本生效",
  slot: "存量生效",
  unconfigured: "未配置",
};

function AgentDialog({
  target,
  open,
  onOpenChange,
  onSaved,
}: {
  target: AgentProfile | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const isEdit = Boolean(target);
  const [name, setName] = useState(target?.name ?? "");
  const [description, setDescription] = useState(target?.description ?? "");
  const [avatar, setAvatar] = useState(target?.avatar ?? "");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!name.trim()) return;
    setSubmitting(true);
    try {
      const payload = { name: name.trim(), description: description.trim() || undefined, avatar: avatar.trim() || undefined };
      if (isEdit && target) {
        await updateAgent(target.id, payload);
        toast.success("已保存");
      } else {
        await createAgent(payload);
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
          <DialogTitle>{isEdit ? "编辑智能体" : "新建智能体"}</DialogTitle>
          <DialogDescription>{isEdit ? target?.name : "创建后需到「提示词」中配置各槽位。"}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="ag-name">名称</Label>
            <Input id="ag-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：客服助手" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ag-desc">描述</Label>
            <Textarea id="ag-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ag-avatar">头像标识</Label>
            <Input id="ag-avatar" value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="≤32 字符，可选" />
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

/** 版本管理对话框：版本历史 + diff 预览 + 激活/回滚（D8 二次确认交互） */
function SlotVersionDialog({
  agentId,
  slot,
  open,
  onOpenChange,
}: {
  agentId: string;
  slot: { slotKey: string; displayName: string };
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [versions, setVersions] = useState<PromptVersionList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // D8 二次确认：先看 diff（pendingAction），再点确认执行
  const [pendingAction, setPendingAction] = useState<{
    kind: "activate" | "rollback";
    version: PromptVersionItem;
    preview: PromptVersionPreview;
  } | null>(null);
  const [acting, setActing] = useState(false);

  const load = useCallback(async () => {
    if (!slot.slotKey) return;
    setLoading(true);
    setError(null);
    setPendingAction(null);
    try {
      setVersions(await listPromptVersions(agentId, slot.slotKey));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载版本失败");
    } finally {
      setLoading(false);
    }
  }, [agentId, slot.slotKey]);

  useEffect(() => {
    if (open && slot.slotKey) queueMicrotask(() => void load());
  }, [open, load, slot.slotKey]);

  const stageAction = async (kind: "activate" | "rollback", version: PromptVersionItem) => {
    try {
      const preview = await previewPromptVersion(agentId, slot.slotKey, version.versionId);
      setPendingAction({ kind, version, preview });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "获取 diff 失败");
    }
  };

  const confirmAction = async () => {
    if (!pendingAction) return;
    setActing(true);
    try {
      const fn =
        pendingAction.kind === "activate" ? activateAgentPromptVersion : rollbackAgentPromptVersion;
      await fn(agentId, slot.slotKey, pendingAction.version.versionId, true);
      toast.success(pendingAction.kind === "activate" ? "已激活（≤5s 生效）" : "已回滚");
      setPendingAction(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActing(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] w-full max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{slot.displayName} · 版本管理</DialogTitle>
          <DialogDescription>
            保存即形成草稿版本；激活/回滚前先看 diff（当前生效 vs 目标版本），确认后 ≤5s 生效。
          </DialogDescription>
        </DialogHeader>
        {pendingAction ? (
          <div className="grid gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge variant={pendingAction.kind === "activate" ? "default" : "secondary"}>
                {pendingAction.kind === "activate" ? "激活" : "回滚"}
              </Badge>
              <span>
                目标 v{pendingAction.version.versionNo ?? "-"}
                {pendingAction.version.content?.split("\n")[0]?.slice(0, 40) ?? "（空白版本）"}
              </span>
              <Badge variant="outline">diff 中的 - 行被移除、+ 行被加入</Badge>
            </div>
            <pre
              className="max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap"
              aria-label="版本 diff 预览"
            >
              {pendingAction.preview.unifiedDiff.length > 0
                ? pendingAction.preview.unifiedDiff.join("\n")
                : "（无差异——目标内容与当前生效一致）"}
            </pre>
          </div>
        ) : loading ? (
          <Loading label="加载版本…" />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : !versions || versions.versions.length === 0 ? (
          <Empty title="该槽位还没有版本" description="先保存一次形成草稿版本" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>版本</TableHead>
                <TableHead>内容首行</TableHead>
                <TableHead>保存人/时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {versions.versions.map((version) => (
                <TableRow key={version.versionId}>
                  <TableCell>
                    v{version.versionNo ?? "-"}
                    {version.isActive && <Badge className="ml-2">生效中</Badge>}
                  </TableCell>
                  <TableCell className="max-w-56 truncate">
                    {version.content?.split("\n")[0] || "（空白版本）"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {version.createBy || "-"}
                    {version.createTime ? ` · ${version.createTime.slice(0, 16).replace("T", " ")}` : ""}
                  </TableCell>
                  <TableCell className="text-right">
                    {version.isActive ? (
                      <span className="text-xs text-muted-foreground">当前生效</span>
                    ) : (
                      <div className="flex justify-end gap-2">
                        <Button variant="outline" size="sm" onClick={() => void stageAction("rollback", version)}>
                          回滚至此
                        </Button>
                        <Button size="sm" onClick={() => void stageAction("activate", version)}>
                          激活
                        </Button>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <DialogFooter>
          {pendingAction ? (
            <>
              <Button variant="outline" onClick={() => setPendingAction(null)} disabled={acting}>
                返回列表
              </Button>
              <Button onClick={confirmAction} disabled={acting}>
                {acting ? "执行中…" : `确认${pendingAction.kind === "activate" ? "激活" : "回滚"} v${pendingAction.version.versionNo ?? "-"}`}
              </Button>
            </>
          ) : (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              关闭
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SlotEditor({
  agentId,
  slot,
  onChanged,
}: {
  agentId: string;
  slot: {
    slotKey: string;
    displayName: string;
    effective: boolean;
    inactiveReason?: string | null;
    content: string;
    contentSource?: string | null;
  };
  onChanged: () => void;
}) {
  const [content, setContent] = useState(slot.content);
  const [saving, setSaving] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      // P10.2 T2.4（D8）：保存 = 形成草稿版本；激活才生效
      const saved = await saveAgentPrompt(agentId, slot.slotKey, content);
      toast.success(
        saved?.versionNo != null ? `已存为草稿 v${saved.versionNo}，激活后生效` : "已保存",
      );
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const resetToDefault = async () => {
    try {
      const dflt = await getDefaultAgentPrompt(slot.slotKey);
      setContent(dflt ?? "");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "获取默认失败");
    }
  };

  return (
    <div className="grid gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{slot.displayName}</span>
          {!slot.effective && <Badge variant="secondary">当前模式不生效</Badge>}
          {slot.contentSource && CONTENT_SOURCE_LABEL[slot.contentSource] && (
            <Badge variant="outline">{CONTENT_SOURCE_LABEL[slot.contentSource]}</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setVersionsOpen(true)}>
            <History />
            版本
          </Button>
          <Button variant="outline" size="sm" onClick={() => void resetToDefault()}>
            <RotateCcw />
            恢复默认
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={saving}>
            <Save />
            保存
          </Button>
        </div>
      </div>
      <Textarea
        className="min-h-24 font-mono text-xs"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        aria-label={`${slot.displayName} 内容`}
        placeholder="提示词内容（空白则回落内置默认；保存形成草稿版本，激活后生效）"
      />
      <SlotVersionDialog
        agentId={agentId}
        slot={{ slotKey: slot.slotKey, displayName: slot.displayName }}
        open={versionsOpen}
        onOpenChange={setVersionsOpen}
      />
    </div>
  );
}

function PromptsDialog({
  agent,
  open,
  onOpenChange,
}: {
  agent: AgentProfile | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [view, setView] = useState<AgentPromptsView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!agent) return;
    setLoading(true);
    setError(null);
    try {
      setView(await getAgentPrompts(agent.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [agent]);

  useEffect(() => {
    if (open && agent) queueMicrotask(() => void load());
  }, [open, agent, load]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] w-full max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>提示词配置</DialogTitle>
          <DialogDescription>
            {view ? `${view.agentName} · ${view.slots.length} 个槽位 · 编排模式 ${view.mode}` : agent?.name}
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <Loading label="加载提示词…" />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : view ? (
          <div className="grid gap-3">
            {view.slots.map((slot) => (
              <SlotEditor key={slot.slotKey} agentId={view.agentId} slot={slot} onChanged={() => void load()} />
            ))}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export default function AgentListPage() {
  const [response, setResponse] = useState<{ mode: string; effectiveSlotTotal: number; agents: AgentProfile[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ open: boolean; target: AgentProfile | null }>({ open: false, target: null });
  const [promptsTarget, setPromptsTarget] = useState<AgentProfile | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AgentProfile | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setResponse(await getAgents());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const doActivate = async (agent: AgentProfile) => {
    try {
      await activateAgent(agent.id);
      toast.success("已激活");
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "激活失败");
    }
  };

  const doDelete = async (agent: AgentProfile) => {
    try {
      await deleteAgent(agent.id);
      toast.success("已删除");
      setDeleteTarget(null);
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  const agents = response?.agents ?? [];
  const slotTotal = response?.effectiveSlotTotal ?? 0;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">智能体档案</h1>
          <p className="text-sm text-muted-foreground">
            管理 Agent Profile 与提示词槽位（编排模式 {response?.mode ?? "-"}）
          </p>
        </div>
        <Button
          onClick={() => setDialog({ open: true, target: null })}
        >
          <Plus />
          新建智能体
        </Button>
      </div>

      {loading ? (
        <Loading label="加载智能体…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : agents.length === 0 ? (
        <Empty title="暂无智能体" description="点击「新建智能体」创建第一个档案" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>描述</TableHead>
                <TableHead>槽位覆盖</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agents.map((agent) => (
                <TableRow key={agent.id}>
                  <TableCell className="font-medium">
                    <span className="flex items-center gap-2">
                      <Bot className="text-muted-foreground size-4" />
                      {agent.name}
                      {agent.builtin && <Badge variant="secondary">内置</Badge>}
                    </span>
                  </TableCell>
                  <TableCell className="max-w-64">
                    <span className="block truncate text-muted-foreground">{agent.description || "-"}</span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {agent.effectiveSlots ?? 0}/{slotTotal}
                    {agent.inactiveSlots ? `（${agent.inactiveSlots} 项当前模式不生效）` : ""}
                  </TableCell>
                  <TableCell>
                    {agent.active ? <Badge>激活中</Badge> : <Badge variant="secondary">未激活</Badge>}
                  </TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                        <MoreHorizontal />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {!agent.active && (
                          <DropdownMenuItem onClick={() => void doActivate(agent)}>
                            <Play />
                            激活
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuItem onClick={() => setPromptsTarget(agent)}>
                          <Save />
                          提示词
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => setDialog({ open: true, target: agent })}>
                          <Pencil />
                          编辑
                        </DropdownMenuItem>
                        <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(agent)}>
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
        </div>
      )}

      {dialog.open && (
        <AgentDialog
          target={dialog.target}
          open
          onOpenChange={(v) => setDialog((d) => ({ ...d, open: v }))}
          onSaved={() => void load()}
        />
      )}

      {promptsTarget && (
        <PromptsDialog agent={promptsTarget} open={Boolean(promptsTarget)} onOpenChange={(v) => { if (!v) setPromptsTarget(null); }} />
      )}

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除智能体</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「{deleteTarget?.name}」吗？激活中的智能体需先激活其他智能体才能删除。
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
