// M4C T9 Agent 调试域类型（对齐 rag/controller/agent_controller.py + agent_service.chat）
// - POST /agent/chat → camelCase AgentResult：answer/steps/iterations/error

/** Agent 执行步骤（AgentStep：camelCase） */
export interface AgentStep {
  tool?: string | null;
  params?: Record<string, unknown> | null;
  observation?: string | null;
  ok?: boolean | null;
}

/** Agent 结果（AgentResult：camelCase） */
export interface AgentResult {
  answer?: string | null;
  iterations?: number | null;
  error?: string | null;
  steps?: AgentStep[] | null;
}
