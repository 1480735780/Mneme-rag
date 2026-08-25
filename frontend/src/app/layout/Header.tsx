// M0 #6 顶部栏：侧栏折叠/移动菜单开关 + 用户头像下拉（含登出）
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuthStore } from "@/features/auth/store";

interface HeaderProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onOpenMobile: () => void;
}

export default function Header({ collapsed, onToggleCollapsed, onOpenMobile }: HeaderProps) {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  const displayName = user?.username || "未登录";
  const avatarUrl = user?.avatar;

  const handleLogout = async () => {
    await logout();
    toast.success("已退出登录");
    navigate("/login", { replace: true });
  };

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between border-b bg-background/80 px-4 backdrop-blur">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon-sm"
          className="hidden lg:inline-flex"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "展开侧栏" : "折叠侧栏"}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          className="lg:hidden"
          onClick={onOpenMobile}
          aria-label="打开菜单"
        >
          <Menu className="size-4" />
        </Button>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" className="gap-2" />}>
          <Avatar size="sm">
            <AvatarImage src={avatarUrl} alt={displayName} />
            <AvatarFallback>{displayName.slice(0, 1).toUpperCase()}</AvatarFallback>
          </Avatar>
          <span className="hidden text-sm font-medium sm:inline">{displayName}</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuLabel>
            <div className="text-sm font-medium">{displayName}</div>
            <div className="text-xs text-muted-foreground">{user?.role || "guest"}</div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => void handleLogout()}>
            <LogOut className="size-4" />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
