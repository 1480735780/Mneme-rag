// v1.1 P2 Agent 输入框：多行自适应 + Enter 发送 / Shift+Enter 换行 + 发送/停止切换
// 无深度思考开关：Agent 自主规划是否思考（对齐 ragent-new AgentChatInput）
// 停止 = cancelGeneration（不中断 fetch，等后端落库后回发 cancel + done 收尾）
// 示例问题预填经 draft.key 重挂载注入初值（避免 effect 内 setState）
import { useEffect, useRef, useState } from "react";
import { ArrowUp, Square } from "lucide-react";

import { Button } from "@/components/ui/button";

import { useAgentChatStore } from "../store";

export default function AgentChatInput() {
  const draft = useAgentChatStore((s) => s.draft);
  // draft 每次设置都换 key → 重挂载注入新初值；同文重复点击也能再次触发
  return <AgentChatInputInner key={draft?.key ?? "blank"} initialDraft={draft?.text ?? ""} />;
}

function AgentChatInputInner({ initialDraft }: { initialDraft: string }) {
  const [value, setValue] = useState(initialDraft);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const isStreaming = useAgentChatStore((s) => s.isStreaming);
  const sendMessage = useAgentChatStore((s) => s.sendMessage);
  const cancelGeneration = useAgentChatStore((s) => s.cancelGeneration);
  const inputFocusKey = useAgentChatStore((s) => s.inputFocusKey);

  const focusInput = () => {
    taRef.current?.focus({ preventScroll: true });
  };

  useEffect(() => {
    const el = taRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    }
  }, [value]);

  useEffect(() => {
    if (!inputFocusKey) return;
    focusInput();
  }, [inputFocusKey]);

  // 示例问题预填聚焦经重挂载 + autoFocus 完成（draft 变化 → key 变化 → 重新挂载），不直接发送

  const submit = async () => {
    const trimmed = value.trim();
    if (!trimmed || isStreaming) return;
    setValue("");
    focusInput();
    await sendMessage(trimmed);
    focusInput();
  };

  return (
    <div className="border-t bg-background p-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-xl border bg-card p-2 shadow-sm">
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送；Shift+Enter 换行；中文输入法组词中 Enter 不发送
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submit();
            }
          }}
          rows={1}
          autoFocus
          placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
          className="max-h-40 min-h-9 flex-1 resize-none bg-transparent py-1.5 text-sm outline-none placeholder:text-muted-foreground"
          aria-label="消息输入框"
        />
        {isStreaming ? (
          <Button
            variant="secondary"
            size="icon"
            onClick={() => {
              cancelGeneration();
              focusInput();
            }}
            aria-label="停止生成"
            title="停止生成"
          >
            <Square className="size-4" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={() => void submit()}
            disabled={!value.trim()}
            aria-label="发送"
            title="发送（Enter）"
          >
            <ArrowUp className="size-4" />
          </Button>
        )}
      </div>
      <p className="mx-auto mt-1.5 max-w-3xl text-[11px] text-muted-foreground">
        内容由 AI 生成，请仔细甄别
      </p>
    </div>
  );
}
