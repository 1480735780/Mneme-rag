// M4C T9 Agent 调试 REST API（对齐 rag/controller/agent_controller.py）
// - POST /agent/chat：JSON 非流式 Agent 闭环
import { post } from "@/shared/api/client";

import type { AgentResult } from "./types";

/** POST /agent/chat：执行 Agent 闭环，返回 answer/steps/iterations/error */
export function agentChat(question: string): Promise<AgentResult> {
  return post("/agent/chat", { question });
}
