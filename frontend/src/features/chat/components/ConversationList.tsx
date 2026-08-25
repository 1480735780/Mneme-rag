// M1 #6 会话侧栏：新建 / 重命名（inline）/ 删除（确认弹窗）/ 选中高亮 / lastTime 倒序
import { useState } from "react";
import { MessageSquare, MessageSquarePlus, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

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

import { useChatStore } from "../store";

export default function ConversationList() {
  const navigate = useNavigate();
  const conversations = useChatStore((s) => s.conversations);
  const activeId = useChatStore((s) => s.activeId);
  const createConversation = useChatStore((s) => s.createConversation);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const removeConversation = useChatStore((s) => s.removeConversation);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [deleting, setDeleting] = useState<{ id: string; title: string } | null>(null);

  const handleCreate = () => {
    createConversation();
    navigate("/chat", { replace: true });
  };

  const commitRename = () => {
    if (editingId && editingTitle.trim()) {
      void renameConversation(editingId, editingTitle.trim());
    }
    setEditingId(null);
  };

  const handleDelete = () => {
    if (!deleting) return;
    const { id } = deleting;
    void removeConversation(id);
    if (activeId === id) {
      navigate("/chat", { replace: true });
    }
    setDeleting(null);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="p-2">
        <Button variant="outline" className="w-full" onClick={handleCreate}>
          <MessageSquarePlus className="size-4" />
          新建会话
        </Button>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
        {conversations.map((c) => {
          const active = c.conversationId === activeId;
          return (
            <div key={c.conversationId} className={cn("group flex items-center rounded-lg", active && "bg-accent")}>
              {editingId === c.conversationId ? (
                <Input
                  autoFocus
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  className="h-8"
                  aria-label="会话名称"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => void selectConversation(c.conversationId)}
                  className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                  <MessageSquare className="size-4 shrink-0" />
                  <span className="truncate">{c.title}</span>
                </button>
              )}
              {editingId !== c.conversationId && (
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
                    <DropdownMenuItem onClick={() => { setEditingId(c.conversationId); setEditingTitle(c.title); }}>
                      <Pencil className="size-4" />
                      重命名
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      variant="destructive"
                      onClick={() => setDeleting({ id: c.conversationId, title: c.title })}
                    >
                      <Trash2 className="size-4" />
                      删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          );
        })}
      </nav>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => { if (!open) setDeleting(null); }}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>删除会话</AlertDialogTitle>
            <AlertDialogDescription>确认删除「{deleting?.title}」？此操作不可恢复。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={handleDelete}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
