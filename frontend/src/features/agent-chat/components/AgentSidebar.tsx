// v1.1 P2 Agent 会话侧栏（移植 ragent-new AgentSidebar：最近分组 / 搜索 / 批量选择删除 / 重命名）
// 用户身份与外链由 AppLayout 全局头承载，此处只做会话域
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, MessageSquare, MessageSquarePlus, MoreHorizontal, Pencil, Search, Trash2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { groupSessions, useAgentChatStore } from "../store";
import type { AgentSession } from "../types";

/** 待确认的删除动作 单会话与批量共用一个弹窗 */
type DeleteTarget = { kind: "one"; id: string; title: string } | { kind: "batch"; ids: string[] };

/** 行上只留标题 全标题/相对时间/轮数收进悬停 tooltip */
function sessionTip(session: AgentSession): string {
  const turns =
    typeof session.turns === "number" && session.turns > 0 ? `×${session.turns} 轮` : "";
  return [session.title || "新会话", relTime(session.lastTime), turns].filter(Boolean).join(" · ");
}

function relTime(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const m = Math.floor((Date.now() - t) / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} 天前`;
  return new Date(iso).toLocaleDateString();
}

export default function AgentSidebar() {
  const sessions = useAgentChatStore((s) => s.sessions);
  const currentSessionId = useAgentChatStore((s) => s.currentSessionId);
  const isStreaming = useAgentChatStore((s) => s.isStreaming);
  const sessionsLoaded = useAgentChatStore((s) => s.sessionsLoaded);
  const startNewChat = useAgentChatStore((s) => s.startNewChat);
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
  const renameSession = useAgentChatStore((s) => s.renameSession);
  const deleteSession = useAgentChatStore((s) => s.deleteSession);
  const batchDeleteSessions = useAgentChatStore((s) => s.batchDeleteSessions);

  const [selectMode, setSelectMode] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (sessions.length === 0 && !sessionsLoaded) {
      void loadSessions();
    }
  }, [loadSessions, sessions.length, sessionsLoaded]);

  // ⌘K / Ctrl+K 全局聚焦搜索
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const exitSelect = () => {
    setSelectMode(false);
    setPicked(new Set());
  };

  const togglePick = (id: string) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openSession = (sessionId: string) => {
    if (isStreaming || sessionId === currentSessionId) return;
    void useAgentChatStore.getState().loadMessages(sessionId);
  };

  const startRename = (session: AgentSession) => {
    setEditingId(session.id);
    setDraft(session.title || "新会话");
  };

  const commitRename = (session: AgentSession) => {
    const next = draft.trim();
    if (next && next !== (session.title || "")) {
      void renameSession(session.id, next);
    }
    setEditingId(null);
  };

  // 单删与批量删都在这落地 删到当前会话就清空消息区
  const runDelete = () => {
    if (!deleteTarget) return;
    const ids = deleteTarget.kind === "one" ? [deleteTarget.id] : deleteTarget.ids;
    const task =
      deleteTarget.kind === "one" ? deleteSession(deleteTarget.id) : batchDeleteSessions(ids);
    setDeleteTarget(null);
    exitSelect();
    void task;
  };

  const keyword = query.trim().toLowerCase();
  const shown = keyword
    ? sessions.filter((session) => (session.title || "新会话").toLowerCase().includes(keyword))
    : sessions;
  const groups = useMemo(() => groupSessions(shown), [shown]);

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r">
      <div className="p-2">
        <Button
          variant="outline"
          className="w-full"
          onClick={() => {
            exitSelect();
            startNewChat();
          }}
        >
          <MessageSquarePlus className="size-4" />
          新建会话
        </Button>
      </div>

      {/* 搜索：输入即过滤 ⌘K 聚焦 批量入口并在标题行右端 */}
      <div className="px-2 pb-1">
        <div className="flex items-center justify-between">
          <span className="px-1 text-xs text-muted-foreground">搜索会话</span>
          {selectMode ? (
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={exitSelect}
            >
              取消
            </button>
          ) : sessions.length > 0 ? (
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => setSelectMode(true)}
            >
              选择
            </button>
          ) : null}
        </div>
        <div className="relative mt-1">
          <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            value={query}
            placeholder="搜索会话…"
            className="h-8 pl-7"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setQuery("");
                e.currentTarget.blur();
              }
            }}
            aria-label="搜索会话"
          />
        </div>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-2">
        {sessions.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
            <MessageSquare className="size-8" strokeWidth={1.25} />
            <p className="text-sm">{!sessionsLoaded ? "加载会话中" : "暂无会话记录"}</p>
          </div>
        ) : shown.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
            <Search className="size-8" strokeWidth={1.25} />
            <p className="text-sm">无匹配会话</p>
          </div>
        ) : (
          groups.map((group) => (
            <div key={group.label}>
              <div className="px-1 pt-2 pb-1 text-[11px] text-muted-foreground">{group.label}</div>
              {group.items.map((session) => {
                const active = session.id === currentSessionId;
                const isEditing = editingId === session.id;
                const checked = picked.has(session.id);
                return (
                  <div
                    key={session.id}
                    className={cn("group flex items-center rounded-lg", active && !selectMode && "bg-accent", checked && "bg-accent")}
                  >
                    {selectMode ? (
                      <button
                        type="button"
                        className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left text-sm text-muted-foreground hover:text-foreground"
                        onClick={() => togglePick(session.id)}
                        aria-pressed={checked}
                      >
                        <span
                          className={cn(
                            "flex size-4 shrink-0 items-center justify-center rounded border",
                            checked && "border-primary bg-primary text-primary-foreground",
                          )}
                          aria-hidden="true"
                        >
                          {checked ? <Check className="size-3" strokeWidth={3} /> : null}
                        </span>
                        <span className="truncate">{session.title || "新会话"}</span>
                      </button>
                    ) : isEditing ? (
                      <Input
                        autoFocus
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onBlur={() => commitRename(session)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename(session);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        className="h-8"
                        aria-label="会话标题"
                      />
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => openSession(session.id)}
                          title={sessionTip(session)}
                          className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground"
                        >
                          <MessageSquare className="size-4 shrink-0" />
                          <span className="truncate">{session.title || "新会话"}</span>
                        </button>
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            render={
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                className="mr-0.5 opacity-100 lg:opacity-0 lg:group-hover:opacity-100"
                                aria-label="会话操作"
                              />
                            }
                          >
                            <MoreHorizontal className="size-4" />
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              onClick={() => {
                                startRename(session);
                              }}
                            >
                              <Pencil className="size-4" />
                              重命名
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={() =>
                                setDeleteTarget({
                                  kind: "one",
                                  id: session.id,
                                  title: session.title || "新会话",
                                })
                              }
                            >
                              <Trash2 className="size-4" />
                              删除
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ))
        )}
      </nav>

      {selectMode ? (
        <div className="flex items-center justify-between border-t px-3 py-2 text-sm">
          <span className="text-muted-foreground">已选 {picked.size}</span>
          <Button
            variant="destructive"
            size="sm"
            disabled={picked.size === 0}
            onClick={() => setDeleteTarget({ kind: "batch", ids: [...picked] })}
          >
            删除选中
          </Button>
        </div>
      ) : null}

      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {deleteTarget?.kind === "batch"
                ? `删除选中的 ${deleteTarget.ids.length} 个会话？`
                : "删除该会话？"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.kind === "batch"
                ? "选中的会话及其全部轨迹将被删除，无法恢复。"
                : `「${deleteTarget?.kind === "one" ? deleteTarget.title : "该会话"}」将被删除，无法恢复。`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={runDelete}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </aside>
  );
}
