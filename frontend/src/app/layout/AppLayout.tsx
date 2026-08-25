// M0 #6 应用主布局：可折叠侧栏 + 顶栏 + 内容区
import { useState } from "react";
import { Outlet } from "react-router-dom";

import Header from "./Header";
import Sidebar from "./Sidebar";

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex min-h-dvh bg-background">
      <Sidebar collapsed={collapsed} mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((c) => !c)}
          onOpenMobile={() => setMobileOpen(true)}
        />
        <main className="flex flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
