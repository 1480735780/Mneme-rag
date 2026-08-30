// v1.1 P2 Agent 页级布局：引擎探活徽标（GET /agent/v1/meta）+ 会话侧栏 + 主区 + 原始帧抽屉
// 与 ragent-new AgentLayout 的差异：外层 AppLayout（全局导航/身份）已由路由提供，此处只做页内布局
import { useState } from "react";

import AgentRawLog from "./AgentRawLog";
import AgentSidebar from "./AgentSidebar";
import { useAgentMeta, type AgentMetaState } from "../hooks/useAgentMeta";

function MetaBadge({ meta }: { meta: AgentMetaState }) {
  const badgeName =
    meta.status === "online" ? meta.meta.framework : meta.status === "probing" ? "探测中" : "引擎离线";

  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border bg-card px-2.5 py-0.5 text-xs font-medium">
        <span
          className={`size-1.5 rounded-full ${
            meta.status === "online"
              ? "bg-emerald-500"
              : meta.status === "probing"
                ? "animate-pulse bg-muted-foreground"
                : "bg-destructive"
          }`}
          aria-hidden="true"
        />
        {badgeName}
      </span>
      {meta.status === "online" ? (
        <span className="truncate text-xs text-muted-foreground" title={meta.meta.model}>
          {meta.meta.model || "未配模型"}
          {meta.meta.mcpConfigured ? " · MCP 工具已接入" : ""}
        </span>
      ) : null}
      {meta.status === "offline" ? (
        <span className="truncate text-xs text-destructive" title={meta.message}>
          {meta.message}
        </span>
      ) : null}
    </div>
  );
}

interface AgentLayoutProps {
  children: React.ReactNode;
}

export default function AgentLayout({ children }: AgentLayoutProps) {
  const [rawOpen, setRawOpen] = useState(false);
  const meta = useAgentMeta();

  return (
    <div className="flex h-full w-full flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b px-4 py-2">
        <MetaBadge meta={meta} />
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {meta.status === "online" ? (
            <span className="hidden text-[11px] text-muted-foreground sm:inline">
              {meta.meta.capabilities.join(" · ")}
            </span>
          ) : null}
          <button
            type="button"
            className={`rounded-md border px-2 py-1 text-xs font-medium transition-colors hover:bg-accent ${
              rawOpen ? "bg-accent text-foreground" : "text-muted-foreground"
            }`}
            onClick={() => setRawOpen((v) => !v)}
            aria-pressed={rawOpen}
          >
            {"{ }"} 原始帧
          </button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <AgentSidebar />
        <main className="flex min-w-0 flex-1 flex-col">{children}</main>
        {rawOpen ? <AgentRawLog onClose={() => setRawOpen(false)} /> : null}
      </div>
    </div>
  );
}
