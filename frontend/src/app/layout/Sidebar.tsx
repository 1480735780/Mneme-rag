// M0 #6 响应式侧边栏：桌面可折叠，移动端抽屉
import { Bot, Database, FileSearch, GitBranch, LayoutDashboard, Map as MapIcon, MessageSquare, Network, ScrollText, Settings, Shapes, Users, Waypoints, Workflow, X } from "lucide-react";
import { NavLink } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/features/auth/store";
import { ROLE_ADMIN } from "@/features/auth/types";
import { cn } from "@/lib/utils";

interface SidebarProps {
  collapsed: boolean;
  mobileOpen: boolean;
  onMobileClose: () => void;
}

interface NavItem {
  to: string;
  label: string;
  icon: typeof MessageSquare;
}

const userItems: NavItem[] = [
  { to: "/chat", label: "对话", icon: MessageSquare },
  { to: "/agent", label: "智能体", icon: Bot },
];
// M3 admin 菜单：仪表盘 / 知识库 / 链路追踪 / 系统设置
const coreAdminItems: NavItem[] = [
  { to: "/admin/dashboard", label: "仪表盘", icon: LayoutDashboard },
  { to: "/admin/knowledge", label: "知识库", icon: Database },
  { to: "/admin/traces", label: "链路追踪", icon: Waypoints },
  { to: "/admin/settings", label: "系统设置", icon: Settings },
];
// M4 平台管理菜单：用户/审计/治理/流水线/图谱
const platformAdminItems: NavItem[] = [
  { to: "/admin/users", label: "用户管理", icon: Users },
  { to: "/admin/change-logs", label: "审计日志", icon: ScrollText },
  { to: "/admin/sample-questions", label: "示例问题", icon: MessageSquare },
  { to: "/admin/mappings", label: "术语映射", icon: Shapes },
  { to: "/admin/intent-tree", label: "意图树", icon: GitBranch },
  { to: "/admin/agents", label: "智能体", icon: Bot },
  { to: "/admin/ingestion", label: "摄取流水线", icon: Workflow },
  { to: "/admin/graph", label: "知识图谱", icon: Network },
  { to: "/admin/agent-debug", label: "Agent 调试", icon: FileSearch },
];

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <div
      className={cn(
        "flex h-14 shrink-0 items-center gap-2 border-b px-3",
        collapsed && "justify-center px-0",
      )}
    >
      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-sm font-bold text-primary-foreground">
        R
      </span>
      {!collapsed && <span className="text-sm font-semibold">mneme-rag</span>}
    </div>
  );
}

function NavLinks({ collapsed }: { collapsed: boolean }) {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === ROLE_ADMIN;
  const items = [...userItems];
  if (isAdmin) {
    items.push(...coreAdminItems);
  }

  return (
    <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 py-3">
      {items.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          title={collapsed ? label : undefined}
          className={({ isActive }) =>
            cn(
              "flex h-9 items-center gap-2 rounded-md px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
              isActive && "bg-accent text-foreground",
              collapsed && "justify-center px-0",
            )
          }
        >
          <Icon className="size-4 shrink-0" />
          {!collapsed && <span>{label}</span>}
        </NavLink>
      ))}
      {isAdmin && (
        <>
          {!collapsed && (
            <p className="mt-2 flex items-center gap-1.5 px-2 text-xs text-muted-foreground">
              <MapIcon className="size-3" />
              平台管理
            </p>
          )}
          {platformAdminItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                cn(
                  "flex h-9 items-center gap-2 rounded-md px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                  isActive && "bg-accent text-foreground",
                  collapsed && "justify-center px-0",
                )
              }
            >
              <Icon className="size-4 shrink-0" />
              {!collapsed && <span>{label}</span>}
            </NavLink>
          ))}
        </>
      )}
    </nav>
  );
}

export default function Sidebar({ collapsed, mobileOpen, onMobileClose }: SidebarProps) {
  return (
    <>
      {/* 桌面端：可折叠侧栏 */}
      <aside
        className={cn(
          "sticky top-0 hidden h-dvh shrink-0 flex-col border-r bg-sidebar transition-[width] duration-200 lg:flex",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <Brand collapsed={collapsed} />
        <NavLinks collapsed={collapsed} />
      </aside>

      {/* 移动端：抽屉 */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <button
            type="button"
            aria-label="关闭菜单"
            className="flex-1 bg-black/40"
            onClick={onMobileClose}
          />
          <div className="flex h-full w-64 flex-col bg-sidebar shadow-lg">
            <div className="flex h-14 items-center justify-between border-b px-3">
              <span className="text-sm font-semibold">mneme-rag</span>
              <Button variant="ghost" size="icon-sm" onClick={onMobileClose} aria-label="关闭菜单">
                <X className="size-4" />
              </Button>
            </div>
            <NavLinks collapsed={false} />
          </div>
        </div>
      )}
    </>
  );
}
