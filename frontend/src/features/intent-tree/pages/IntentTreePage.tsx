// M4B T6 意图树页：树展示（缩进）/ 节点创建/编辑 / 批量启停/删除（二次确认）
import { useCallback, useEffect, useState } from "react";
import { GitBranch, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";

import { batchDeleteIntentNodes, batchDisableIntentNodes, batchEnableIntentNodes, createIntentNode, deleteIntentNode, getIntentTree, updateIntentNode } from "../api";
import { INTENT_KINDS, INTENT_LEVELS, type IntentNode, type IntentNodePayload } from "../types";

function levelMeta(level?: number | null) {
  return INTENT_LEVELS.find((l) => l.value === level) ?? { value: -1, label: "未知" };
}

function kindMeta(kind?: number | null) {
  return INTENT_KINDS.find((k) => k.value === kind) ?? { value: -1, label: "未知" };
}

/** 递归展平树为带缩进深度的行 */
function flatten(nodes: IntentNode[], depth = 0, out: Array<{ node: IntentNode; depth: number }> = []) {
  for (const n of nodes) {
    out.push({ node: n, depth });
    if (n.children?.length) flatten(n.children, depth + 1, out);
  }
  return out;
}

function NodeDialog({
  target,
  parent,
  open,
  onOpenChange,
  onSaved,
}: {
  target: IntentNode | null;
  parent: IntentNode | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const isEdit = Boolean(target);
  const [intentCode, setIntentCode] = useState(target?.intentCode ?? "");
  const [name, setName] = useState(target?.name ?? "");
  const [level, setLevel] = useState<string>(target?.level !== undefined && target?.level !== null ? String(target.level) : parent ? String((parent.level ?? 0) + 1) : "0");
  const [kind, setKind] = useState<string>(target?.kind !== undefined && target?.kind !== null ? String(target.kind) : "0");
  const [description, setDescription] = useState(target?.description ?? "");
  const [examplesText, setExamplesText] = useState((target?.examples ?? []).join("\n"));
  const [topK, setTopK] = useState(target?.topK !== undefined && target?.topK !== null ? String(target.topK) : "");
  const [enabled, setEnabled] = useState(target?.enabled ?? true);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!intentCode.trim() || !name.trim()) return;
    setSubmitting(true);
    try {
      const examples = examplesText
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      const payload: IntentNodePayload = {
        intentCode: intentCode.trim(),
        name: name.trim(),
        level: Number(level),
        kind: Number(kind),
        description: description.trim() || undefined,
        examples: examples.length ? examples : undefined,
        topK: topK === "" || Number.isNaN(Number(topK)) ? undefined : Number(topK),
        enabled: enabled ? 1 : 0,
        parentCode: parent?.intentCode || undefined,
      };
      if (isEdit && target) {
        await updateIntentNode(target.id, payload);
        toast.success("已保存");
      } else {
        await createIntentNode(payload);
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
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑意图节点" : parent ? `新增子节点（${parent.name || parent.intentCode}）` : "新建根节点"}</DialogTitle>
          <DialogDescription>层级与类型决定该节点在检索编排中的角色。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="it-code">意图标识（intentCode）</Label>
            <Input id="it-code" value={intentCode} onChange={(e) => setIntentCode(e.target.value)} disabled={isEdit} placeholder="全局唯一，如 product_query" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="it-name">名称</Label>
            <Input id="it-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：产品咨询" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label>层级</Label>
              <Select value={level} onValueChange={(v) => setLevel(v ?? "0")}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {INTENT_LEVELS.map((l) => (
                    <SelectItem key={l.value} value={String(l.value)}>
                      {l.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>类型</Label>
              <Select value={kind} onValueChange={(v) => setKind(v ?? "0")}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {INTENT_KINDS.map((k) => (
                    <SelectItem key={k.value} value={String(k.value)}>
                      {k.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="it-desc">描述</Label>
            <Textarea id="it-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="it-examples">示例问题（每行一条）</Label>
            <Textarea id="it-examples" value={examplesText} onChange={(e) => setExamplesText(e.target.value)} placeholder="用于识别该意图的典型提问" />
          </div>
          <div className="grid grid-cols-2 items-end gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="it-topk">节点级 TopK</Label>
              <Input id="it-topk" type="number" value={topK} onChange={(e) => setTopK(e.target.value)} placeholder="留空回退全局" />
            </div>
            <div className="flex items-end gap-2 pb-1">
              <Checkbox id="it-enabled" checked={enabled} onCheckedChange={(v) => setEnabled(v === true)} />
              <Label htmlFor="it-enabled" className="cursor-pointer">
                启用
              </Label>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !intentCode.trim() || !name.trim()}>
            {submitting ? "保存中…" : isEdit ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function IntentTreePage() {
  const [rows, setRows] = useState<Array<{ node: IntentNode; depth: number }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dialog, setDialog] = useState<{ open: boolean; target: IntentNode | null; parent: IntentNode | null }>({ open: false, target: null, parent: null });
  const [deleteTarget, setDeleteTarget] = useState<IntentNode | null>(null);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const tree = await getIntentTree();
      setRows(flatten(tree));
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) => (prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.node.id))));
  };

  const runBatch = async (fn: (ids: string[]) => Promise<void>, label: string) => {
    const ids = [...selected];
    if (!ids.length) return;
    try {
      await fn(ids);
      toast.success(label);
      setSelected(new Set());
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `${label}失败`);
    }
  };

  const doDelete = async (node: IntentNode) => {
    try {
      await deleteIntentNode(node.id);
      toast.success("已删除");
      setDeleteTarget(null);
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  const doBatchDelete = async () => {
    await runBatch(batchDeleteIntentNodes, "已批量删除");
    setBatchDeleteOpen(false);
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">意图树</h1>
          <p className="text-sm text-muted-foreground">维护意图识别树：领域 → 类目 → 主题，主题可绑定知识库</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Checkbox
            id="it-select-all"
            checked={rows.length > 0 && selected.size === rows.length}
            onCheckedChange={toggleAll}
          />
          <Label htmlFor="it-select-all" className="cursor-pointer text-sm text-muted-foreground">
            全选（{selected.size}）
          </Label>
          <Button variant="outline" size="sm" disabled={!selected.size} onClick={() => void runBatch(batchEnableIntentNodes, "已启用")}>
            批量启用
          </Button>
          <Button variant="outline" size="sm" disabled={!selected.size} onClick={() => void runBatch(batchDisableIntentNodes, "已停用")}>
            批量停用
          </Button>
          <Button variant="outline" size="sm" className="text-destructive" disabled={!selected.size} onClick={() => setBatchDeleteOpen(true)}>
            批量删除
          </Button>
          <Button
            size="sm"
            onClick={() => setDialog({ open: true, target: null, parent: null })}
          >
            <Plus />
            新建根节点
          </Button>
        </div>
      </div>

      {loading ? (
        <Loading label="加载意图树…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : rows.length === 0 ? (
        <Empty title="暂无意图节点" description="点击「新建根节点」开始搭建意图树" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full caption-bottom text-sm">
            <thead className="[&_tr]:border-b">
              <tr className="border-b transition-colors hover:bg-muted/50">
                <th className="h-9 w-10 px-2 text-left" />
                <th className="h-9 px-2 text-left align-middle font-medium whitespace-nowrap text-muted-foreground">节点</th>
                <th className="h-9 px-2 text-left align-middle font-medium whitespace-nowrap text-muted-foreground">层级</th>
                <th className="h-9 px-2 text-left align-middle font-medium whitespace-nowrap text-muted-foreground">类型</th>
                <th className="h-9 px-2 text-left align-middle font-medium whitespace-nowrap text-muted-foreground">状态</th>
                <th className="h-9 w-16 px-2 text-right align-middle font-medium whitespace-nowrap text-muted-foreground">操作</th>
              </tr>
            </thead>
            <tbody className="[&_tr:last-child]:border-0">
              {rows.map(({ node, depth }) => {
                const lm = levelMeta(node.level);
                const km = kindMeta(node.kind);
                return (
                  <tr key={node.id} className="border-b transition-colors hover:bg-muted/50">
                    <td className="px-2 py-1">
                      <Checkbox checked={selected.has(node.id)} onCheckedChange={() => toggleSelect(node.id)} aria-label={`选择 ${node.name ?? node.intentCode}`} />
                    </td>
                    <td className="px-2 py-1">
                      <span className="flex items-center gap-2" style={{ paddingLeft: depth * 20 }}>
                        <GitBranch className="text-muted-foreground size-4 shrink-0" />
                        <span className="font-medium">{node.name || node.intentCode || "-"}</span>
                        {node.intentCode && <span className="font-mono text-xs text-muted-foreground">{node.intentCode}</span>}
                      </span>
                    </td>
                    <td className="px-2 py-1">
                      <Badge variant="secondary">{lm.label}</Badge>
                    </td>
                    <td className="px-2 py-1">
                      <Badge variant="outline">{km.label}</Badge>
                    </td>
                    <td className="px-2 py-1">
                      {node.enabled ? <Badge>启用</Badge> : <Badge variant="secondary">停用</Badge>}
                    </td>
                    <td className="px-2 py-1 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                          <MoreHorizontal />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setDialog({ open: true, target: node, parent: null })}>
                            <Pencil />
                            编辑
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setDialog({ open: true, target: null, parent: node })}>
                            <Plus />
                            新增子节点
                          </DropdownMenuItem>
                          <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(node)}>
                            <Trash2 />
                            删除
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {dialog.open && (
        <NodeDialog
          target={dialog.target}
          parent={dialog.parent}
          open
          onOpenChange={(v) => setDialog((d) => ({ ...d, open: v }))}
          onSaved={() => void load()}
        />
      )}

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除意图节点</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「{deleteTarget?.name || deleteTarget?.intentCode || ""}」吗？存在未删子节点将被拒绝。
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

      <AlertDialog open={batchDeleteOpen} onOpenChange={setBatchDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>批量删除节点</AlertDialogTitle>
            <AlertDialogDescription>确定删除选中的 {selected.size} 个节点吗？需勾选完整子树，否则将被拒绝。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void doBatchDelete()}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
