// M3 T3 Chat → Trace 跳转：仅 admin 显示，带当前任务过滤参数跳转追踪列表
// - 有 streamTaskId（本问答 run）→ 按 taskId 精确过滤
// - 否则有 activeId（会话）→ 按 conversationId 过滤
import { Waypoints } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/features/auth/store";
import { ROLE_ADMIN } from "@/features/auth/types";

import { useChatStore } from "../store";

export function ChatTraceLink() {
  const user = useAuthStore((s) => s.user);
  const streamTaskId = useChatStore((s) => s.streamTaskId);
  const activeId = useChatStore((s) => s.activeId);

  if (user?.role !== ROLE_ADMIN) return null;

  const params = new URLSearchParams();
  if (streamTaskId) params.set("taskId", streamTaskId);
  else if (activeId) params.set("conversationId", activeId);
  const query = params.toString();

  return (
    <Button
      variant="ghost"
      size="sm"
      nativeButton={false}
      render={<Link to={query ? `/admin/traces?${query}` : "/admin/traces"} />}
    >
      <Waypoints />
      链路追踪
    </Button>
  );
}
