// M1 #5 底部输入框：多行自适应 + Enter 发送 / Shift+Enter 换行 + 发送/停止切换 + 深度思考开关
import { useEffect, useRef, useState } from "react";
import { Brain, Send, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { useChatStore } from "../store";

interface Props {
  onSend: (text: string, deepThinking: boolean) => void;
}

export default function ChatInput({ onSend }: Props) {
  const isStreaming = useChatStore((s) => s.isStreaming);
  const stopGeneration = useChatStore((s) => s.stopGeneration);
  const [text, setText] = useState("");
  const [deepThinking, setDeepThinking] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = taRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    }
  }, [text]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;
    onSend(trimmed, deepThinking);
    setText("");
  };

  return (
    <div className="border-t bg-background p-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-xl border bg-card p-2 shadow-sm">
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送；Shift+Enter 换行；中文输入法组词中 Enter 不发送
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          className="max-h-40 min-h-9 flex-1 resize-none bg-transparent py-1.5 text-sm outline-none placeholder:text-muted-foreground"
          aria-label="问题输入框"
        />
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            aria-pressed={deepThinking}
            aria-label="深度思考"
            onClick={() => setDeepThinking((d) => !d)}
            className={cn(
              "inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
              deepThinking && "bg-muted text-primary",
            )}
          >
            <Brain className="size-3.5" />
            深度思考
          </button>
          {isStreaming ? (
            <Button
              variant="secondary"
              size="icon"
              onClick={() => void stopGeneration()}
              aria-label="停止生成"
            >
              <Square className="size-4" />
            </Button>
          ) : (
            <Button size="icon" onClick={submit} disabled={!text.trim()} aria-label="发送">
              <Send className="size-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
