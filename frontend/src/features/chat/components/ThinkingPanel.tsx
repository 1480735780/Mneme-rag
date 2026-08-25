// M1 #4 可折叠深度思考面板
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface Props {
  thinking: string;
  duration?: number;
  isStreaming?: boolean;
}

export default function ThinkingPanel({ thinking, duration, isStreaming }: Props) {
  const [open, setOpen] = useState(false);
  if (!thinking && !isStreaming) return null;

  return (
    <div className="mb-2 rounded-lg border bg-muted/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
        <span>深度思考</span>
        {duration != null && <span className="text-muted-foreground/70">({duration}s)</span>}
        {isStreaming && <span className="animate-pulse">…</span>}
      </button>
      {open && (
        <div className="border-t px-3 py-2 text-sm whitespace-pre-wrap text-muted-foreground">
          {thinking}
        </div>
      )}
    </div>
  );
}
