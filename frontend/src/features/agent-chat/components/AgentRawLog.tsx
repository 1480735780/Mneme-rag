// v1.1 P2 原始帧抽屉：照单全收本次连接的每一条 SSE 帧 供深度核对（移植 ragent-new AgentRawLog）
import { useEffect, useRef } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";

import { useAgentChatStore } from "../store";

function renderData(data: unknown): string {
  if (typeof data === "string") return data;
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}

export default function AgentRawLog({ onClose }: { onClose: () => void }) {
  const frames = useAgentChatStore((s) => s.frames);
  const bodyRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  // 贴着底就跟随最新帧 往上翻查历史时不打扰
  useEffect(() => {
    const el = bodyRef.current;
    if (el && stickRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [frames.length]);

  const onScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-l bg-muted/30" aria-label="原始帧日志">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-xs font-semibold text-muted-foreground">
          原始帧 · {frames.length}
        </span>
        <Button variant="ghost" size="icon-xs" onClick={onClose} aria-label="关闭原始帧日志">
          <X className="size-4" />
        </Button>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-2" ref={bodyRef} onScroll={onScroll}>
        {frames.length === 0 ? (
          <div className="py-8 text-center text-xs text-muted-foreground">
            还没有帧，发一条消息看看。
          </div>
        ) : (
          frames.map((frame) => (
            <div key={frame.id} className="rounded-lg border bg-card px-2 py-1.5">
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span className="tabular-nums">{frame.ts}</span>
                <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-foreground/80">
                  {frame.name}
                </span>
              </div>
              <pre className="mt-1 max-h-40 overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-muted-foreground">
                {renderData(frame.data)}
              </pre>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
