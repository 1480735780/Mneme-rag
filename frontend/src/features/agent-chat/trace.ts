// v1.1 P2 轨迹行纯函数（自 AgentTurn 抽出供测试与列表复用；组件文件只导出组件满足 react-refresh）
import type { AgentBlockUI, AgentMessage } from "./types";

export interface AgentTurn {
  id: string;
  index: number;
  user?: AgentMessage;
  assistant?: AgentMessage;
}

export type TraceChannel = "user" | "reasoning" | "tool" | "answer" | "hint" | "error";

export interface TraceRow {
  key: string;
  channel: TraceChannel;
  ts: string;
  text?: string;
  block?: AgentBlockUI;
  // 连续同名同结果的工具行折叠计数
  count: number;
  streaming?: boolean;
}

export function toHms(value?: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`;
}

/** 耗时刻度：10s 内留一位小数 1m 起转 m/s 复合 */
export function fmtDur(ms?: number): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m${String(secs % 60).padStart(2, "0")}s`;
}

/**
 * 一轮的轨迹行：用户行 + 助手时间线块 依消息态补 等待/错误 合成行
 * 连续、同名、同结果的工具块折叠成一条 ×N（同错刷屏收成一行）
 */
export function buildRows(turn: AgentTurn): TraceRow[] {
  const rows: TraceRow[] = [];
  if (turn.user) {
    rows.push({
      key: `u-${turn.user.id}`,
      channel: "user",
      ts: toHms(turn.user.createdAt),
      text: turn.user.content,
      count: 1,
    });
  }
  const assistant = turn.assistant;
  if (!assistant) return rows;

  const blocks = assistant.blocks ?? [];
  const isStreaming = assistant.status === "streaming";

  blocks.forEach((block, i) => {
    const channel: TraceChannel =
      block.kind === "tool"
        ? "tool"
        : block.kind === "reasoning"
          ? "reasoning"
          : block.kind === "hint"
            ? "hint"
            : "answer";
    const last = rows[rows.length - 1];
    if (
      channel === "tool" &&
      last?.channel === "tool" &&
      last.block?.name === block.name &&
      last.block?.result === block.result &&
      last.block?.status === block.status
    ) {
      last.count += 1;
      return;
    }
    rows.push({
      key: `b-${block.id}`,
      channel,
      ts: block.at,
      text: block.text,
      block,
      count: 1,
      // 最后一个块在流式中即活动轨迹 节点呼吸
      streaming: isStreaming && i === blocks.length - 1,
    });
  });

  if (isStreaming && blocks.length === 0) {
    rows.push({
      key: `wait-${assistant.id}`,
      channel: "hint",
      ts: "",
      text: "等待响应…",
      count: 1,
      streaming: true,
    });
  }
  if (assistant.status === "error") {
    rows.push({
      key: `err-${assistant.id}`,
      channel: "error",
      ts: "",
      text: "生成失败，请稍后重试",
      count: 1,
    });
  }
  return rows;
}

// 用户消息开启新一轮 紧随其后的助手消息配对入同一张 Turn 卡
export function groupTurns(messages: AgentMessage[]): AgentTurn[] {
  const turns: AgentTurn[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      turns.push({ id: message.id, index: turns.length + 1, user: message });
    } else {
      const last = turns[turns.length - 1];
      if (last && !last.assistant) {
        last.assistant = message;
      } else {
        turns.push({ id: message.id, index: turns.length + 1, assistant: message });
      }
    }
  }
  return turns;
}
