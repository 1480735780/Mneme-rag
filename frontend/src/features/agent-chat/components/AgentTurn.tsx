// v1.1 P2 一轮对话的轨迹卡（移植 ragent-new AgentTurn：示波器时间轴行模型）
// 通道：user ▷ / reasoning ○ / tool ● / answer ▮ / hint · / error ✕
// 行模型（buildRows/TraceRow）与工具折叠规则在 ../trace.ts（纯函数可单测）
import { useMemo, useState } from "react";

import { Markdown } from "@/features/chat/components/Markdown";
import { cn } from "@/lib/utils";

import { useAgentChatStore } from "../store";
import { buildRows, fmtDur, type TraceRow } from "../trace";
import type { AgentBlockUI } from "../types";
import type { AgentTurn } from "../trace";

const GLYPH: Record<TraceRow["channel"], string> = {
  user: "▷",
  reasoning: "○",
  tool: "●",
  answer: "▮",
  hint: "·",
  error: "✕",
};

const NAME: Record<TraceRow["channel"], string> = {
  user: "you",
  reasoning: "reasoning",
  tool: "tool",
  answer: "answer",
  hint: "hint",
  error: "error",
};

/** 一轮用户↔助手收进一张卡：轮次头 + 各通道轨迹行 */
export function AgentTurnItem({ turn }: { turn: AgentTurn }) {
  const rows = buildRows(turn);
  const headTs = rows[0]?.ts || "";
  const assistantId = turn.assistant?.id;
  // 流式中不显示总耗时 收尾实测或回放差值就绪后才亮
  const elapsed = turn.assistant?.status === "streaming" ? "" : fmtDur(turn.assistant?.elapsedMs);

  return (
    <section className="rounded-xl border bg-card">
      <header className="flex items-center gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
        <span className="font-semibold tracking-wide text-foreground/80">TURN {turn.index}</span>
        <span className="ml-auto tabular-nums">
          {headTs}
          {elapsed ? <span> · {elapsed}</span> : null}
        </span>
      </header>
      <div className="flex flex-col gap-1 px-3 py-2">
        {rows.map((row, i) => (
          <TraceRowItem key={row.key} row={row} messageId={assistantId} showTs={i > 0} />
        ))}
      </div>
    </section>
  );
}

function TraceRowItem({
  row,
  messageId,
  showTs,
}: {
  row: TraceRow;
  messageId?: string;
  showTs: boolean;
}) {
  const failed = row.channel === "tool" && row.block?.status === "failed";
  const interrupted = row.channel === "tool" && row.block?.status === "interrupted";
  const running = row.channel === "tool" && row.block?.status === "running";

  return (
    <div className="flex gap-2" data-channel={row.channel}>
      <div className="flex w-4 shrink-0 justify-center pt-1">
        <span
          className={cn(
            "text-xs leading-none",
            running && "animate-pulse text-primary",
            failed && "text-destructive",
            row.channel === "user" && "text-foreground",
            row.channel === "answer" && "text-foreground",
            row.channel === "hint" && "text-muted-foreground",
            row.channel === "error" && "text-destructive",
          )}
          aria-hidden="true"
        >
          {GLYPH[row.channel]}
        </span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="font-medium">{NAME[row.channel]}</span>
          {row.channel === "tool" && row.block?.name ? (
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground/80">
              {row.block.name}
            </span>
          ) : null}
          {row.channel === "tool" && row.block?.displayName && row.block.displayName !== row.block.name ? (
            <span>{row.block.displayName}</span>
          ) : null}
          {failed ? <span className="text-destructive">失败</span> : null}
          {interrupted ? <span className="text-destructive">已中断</span> : null}
          {row.channel === "tool" && row.block?.status === "done" ? (
            <span className="text-emerald-600 dark:text-emerald-400">完成</span>
          ) : null}
          {running ? <span className="text-primary">运行中</span> : null}
          {row.block?.durationMs != null ? <span>· {fmtDur(row.block.durationMs)}</span> : null}
          {row.count > 1 ? <span>×{row.count}</span> : null}
          {showTs && row.ts ? <span className="ml-auto tabular-nums">{row.ts}</span> : null}
        </div>
        <RowBody row={row} messageId={messageId} />
      </div>
    </div>
  );
}

function RowBody({ row, messageId }: { row: TraceRow; messageId?: string }) {
  if (row.channel === "tool" && row.block) {
    return <ToolCallBox block={row.block} messageId={messageId} />;
  }
  if (row.channel === "reasoning" && row.block) {
    return <ReasoningRow block={row.block} messageId={messageId} streaming={row.streaming} />;
  }
  if (row.channel === "answer") {
    return (
      <div className="py-0.5 text-sm">
        <Markdown content={row.text ?? ""} />
      </div>
    );
  }
  return (
    <div className="py-0.5 text-sm whitespace-pre-wrap text-muted-foreground">{row.text}</div>
  );
}

/**
 * 思考轨迹：正在想时强制展开实时滚字 块结束自动收成一行摘要 点开可重看
 */
function ReasoningRow({
  block,
  messageId,
  streaming,
}: {
  block: AgentBlockUI;
  messageId?: string;
  streaming?: boolean;
}) {
  const toggleBlockOpen = useAgentChatStore((state) => state.toggleBlockOpen);
  const [localOpen, setLocalOpen] = useState(false);
  const open = Boolean(block.open) || Boolean(streaming) || localOpen;

  return (
    <div>
      <button
        type="button"
        className="mt-0.5 flex max-w-full items-center gap-1 rounded px-1 py-0.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        onClick={() => {
          if (messageId) toggleBlockOpen(messageId, block.id);
          setLocalOpen((v) => !v);
        }}
        aria-expanded={open}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="truncate">{peek(block.text ?? "")}</span>
      </button>
      {open ? (
        <div className="border-l pl-2 text-sm text-muted-foreground">
          <Markdown content={block.text ?? ""} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * 工具块：一行结果摘要 + 展开看完整返回 失败时错因直接显在摘要位（不展开也看得到）
 */
function ToolCallBox({ block, messageId }: { block: AgentBlockUI; messageId?: string }) {
  const toggleBlockOpen = useAgentChatStore((state) => state.toggleBlockOpen);
  const [localOpen, setLocalOpen] = useState(false);
  const open = Boolean(block.open) || localOpen;
  const failed = block.status === "failed";
  const raw = block.result ?? "";
  const parsed = useMemo(() => tryParse(raw), [raw]);

  if (block.status === "running") {
    return (
      <div className="mt-0.5 rounded-lg border bg-muted/40 px-2 py-1.5 text-xs text-muted-foreground">
        执行中…
      </div>
    );
  }

  const summary = failed ? errorSummary(raw) : summarize(parsed, raw);
  const full = parsed != null ? stringify(parsed) : raw;

  return (
    <div className="mt-0.5 rounded-lg border bg-muted/40">
      <button
        type="button"
        className="flex w-full items-center gap-1 px-2 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
        onClick={() => {
          if (messageId) toggleBlockOpen(messageId, block.id);
          setLocalOpen((v) => !v);
        }}
        aria-expanded={open}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className={cn("truncate", failed && "text-destructive")}>{summary}</span>
      </button>
      {open ? (
        <pre className="max-h-72 overflow-auto border-t px-2 py-1.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-muted-foreground">
          {full || "（空返回）"}
        </pre>
      ) : null}
    </div>
  );
}

/** 单行去 markdown 记号：标题/列表前缀与强调符 折叠摘要不该露原始符号 */
function stripMdMarks(line: string): string {
  return line
    .replace(/^#{1,6}\s*/, "")
    .replace(/^[-*+]\s+/, "")
    .replace(/[*_`>]/g, "")
    .trim();
}

/** 压平 markdown 文本为一行：丢分隔线 逐行去记号后以空格拼接 */
function flattenMd(text: string): string {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s && !/^(-{3,}|\*{3,}|_{3,})$/.test(s))
    .map(stripMdMarks)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

/** 取首个非空行、去掉 markdown 记号 作为思考折叠态的一行摘要 */
function peek(text: string): string {
  const line =
    text
      .split("\n")
      .map((s) => s.trim())
      .find(Boolean) ?? "";
  const clean = stripMdMarks(line);
  return clean.length > 84 ? `${clean.slice(0, 84)}…` : clean || "思考中";
}

function tryParse(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** 去掉可能的 Error 前缀 只留人能看懂的错因 */
function errorSummary(text: string): string {
  const t = text.replace(/^\s*error[:：]\s*/i, "").trim();
  return t.length > 120 ? `${t.slice(0, 120)}…` : t || "工具执行出错";
}

function summarize(json: unknown, text: string): string {
  if (json == null) {
    const t = flattenMd(text);
    return t.length > 96 ? `${t.slice(0, 96)}…` : t || "（空返回）";
  }
  if (Array.isArray(json)) return `数组 · ${json.length} 项`;
  if (typeof json === "object") {
    const compact = JSON.stringify(json);
    if (compact.length <= 96) return compact;
    const keys = Object.keys(json as object);
    return `对象 · ${keys.slice(0, 4).join(", ")}${keys.length > 4 ? "…" : ""}`;
  }
  return String(json);
}

function stringify(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
