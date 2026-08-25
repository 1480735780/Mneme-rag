// M4B T7 智能体档案页：列表/创建/编辑/激活/删除（二次确认）+ 槽位提示词编辑
import { useCallback, useEffect, useState } from "react";
import { Bot, MoreHorizontal, Pencil, Play, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
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

import { activateAgent, createAgent, deleteAgent, getAgentPrompts, getAgents, getDefaultAgentPrompt, saveAgentPrompt, updateAgent } from "../api";
import type { AgentProfile, AgentPromptsView } from "../types";

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

function SlotEditor({
  agentId,
  slot,
  onChanged,
}: {
  agentId: string;
  slot: { slotKey: string; displayName: string; effective: boolean; inactiveReason?: string | null; content: string };
  onChanged: () => void;
}) {
  const [content, setContent] = useState(slot.content);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await saveAgentPrompt(agentId, slot.slotKey, content);
      toast.success("已保存");
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
        </div>
        <div className="flex items-center gap-2">
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
        placeholder="提示词内容（空白则回落内置默认）"
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
