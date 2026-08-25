// M4A T2 用户管理页：分页/搜索/创建/编辑（角色+头像+重置密码）/删除（二次确认）
import { useCallback, useEffect, useState } from "react";
import { MoreHorizontal, Pencil, Plus, Search, Shield, Trash2, User as UserIcon } from "lucide-react";
import { toast } from "sonner";

import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorState, Loading } from "@/shared/components/AsyncState";
import { formatDateTime } from "@/shared/format";

import { createUser, deleteUser, getUsersPage, updateUser } from "../api";
import type { User } from "../types";

const PAGE_SIZE = 10;
const ROLE_OPTIONS = [
  { value: "admin", label: "管理员" },
  { value: "user", label: "普通用户" },
];

function roleMeta(role?: string | null) {
  return ROLE_OPTIONS.find((r) => r.value === role) ?? { value: role ?? "", label: role ?? "未知" };
}

function CreateDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<string | undefined>(undefined);
  const [avatar, setAvatar] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!username.trim() || !password) return;
    setSubmitting(true);
    try {
      await createUser({
        username: username.trim(),
        password,
        role: role || undefined,
        avatar: avatar.trim() || undefined,
      });
      toast.success("用户创建成功");
      onOpenChange(false);
      onCreated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>创建用户</DialogTitle>
          <DialogDescription>填写用户名与初始密码，可选指定角色与头像标识。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="user-name">用户名</Label>
            <Input id="user-name" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="例如：alice" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="user-pass">密码</Label>
            <Input id="user-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="初始密码" />
          </div>
          <div className="grid gap-1.5">
            <Label>角色</Label>
            <Select value={role ?? ""} onValueChange={(v) => setRole(v || undefined)}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="普通用户（默认）" />
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((r) => (
                  <SelectItem key={r.value} value={r.value}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="user-avatar">头像标识（可选）</Label>
            <Input id="user-avatar" value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="≤32 字符" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting || !username.trim() || !password}>
            {submitting ? "创建中…" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditDialog({ user, open, onOpenChange, onUpdated }: { user: User | null; open: boolean; onOpenChange: (v: boolean) => void; onUpdated: () => void }) {
  const [role, setRole] = useState<string | undefined>(user?.role ?? undefined);
  const [avatar, setAvatar] = useState(user?.avatar ?? "");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!user) return;
    setSubmitting(true);
    try {
      await updateUser(user.id, {
        role: role || undefined,
        avatar: avatar.trim() || undefined,
        password: password || undefined,
      });
      toast.success("已保存");
      onOpenChange(false);
      onUpdated();
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
          <DialogTitle>编辑用户</DialogTitle>
          <DialogDescription>{user?.username}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label>角色</Label>
            <Select value={role ?? ""} onValueChange={(v) => setRole(v || undefined)}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="请选择" />
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((r) => (
                  <SelectItem key={r.value} value={r.value}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="edit-avatar">头像标识</Label>
            <Input id="edit-avatar" value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="≤32 字符" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="edit-pass">重置密码（可选）</Label>
            <Input id="edit-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="留空则不修改" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function UserListPage() {
  const [records, setRecords] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(0);
  const [current, setCurrent] = useState(1);
  const [name, setName] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<User | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getUsersPage({ current, size: PAGE_SIZE, keyword: keyword || undefined });
      setRecords(page.records);
      setTotal(page.total);
      const p = Math.max(1, Math.ceil(page.total / page.size));
      setPages(p);
      if (page.total > 0 && current > p) {
        setCurrent(p);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [current, keyword]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const onSearch = () => {
    setCurrent(1);
    setKeyword(name.trim());
  };

  const doDelete = async (user: User) => {
    try {
      await deleteUser(user.id);
      toast.success("已删除");
      setDeleteTarget(null);
      void load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">用户管理</h1>
          <p className="text-sm text-muted-foreground">创建、编辑与删除系统用户，按角色分配权限</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              className="w-56 pl-8"
              placeholder="搜索用户名"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onSearch()}
            />
          </div>
          <Button variant="outline" onClick={onSearch}>
            搜索
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus />
            新建用户
          </Button>
        </div>
      </div>

      {loading ? (
        <Loading label="加载用户…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : records.length === 0 ? (
        <Empty title="暂无用户" description="点击「新建用户」创建第一个账号" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>用户名</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="w-16 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((user) => {
                const meta = roleMeta(user.role);
                const isAdmin = meta.value === "admin";
                return (
                  <TableRow key={user.id}>
                    <TableCell>
                      <span className="flex items-center gap-2 font-medium">
                        <UserIcon className="text-muted-foreground size-4" />
                        {user.username}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant={isAdmin ? "default" : "secondary"}>
                        <Shield className="size-3" />
                        {meta.label}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(user.createTime)}</TableCell>
                    <TableCell className="text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="操作" />}>
                          <MoreHorizontal />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setEditTarget(user)}>
                            <Pencil />
                            编辑
                          </DropdownMenuItem>
                          <DropdownMenuItem variant="destructive" onClick={() => setDeleteTarget(user)}>
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

      {createOpen && (
        <CreateDialog open onOpenChange={setCreateOpen} onCreated={() => { setCurrent(1); void load(); }} />
      )}

      {editTarget && (
        <EditDialog
          user={editTarget}
          open={Boolean(editTarget)}
          onOpenChange={(v) => {
            if (!v) setEditTarget(null);
          }}
          onUpdated={() => void load()}
        />
      )}

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(v) => { if (!v) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除用户</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除「{deleteTarget?.username}」吗？该操作不可恢复。
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
