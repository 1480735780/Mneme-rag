// M4B T7 智能体档案域类型（对齐 agent_profile_admin_service + agent_profile_controller）
// - 响应为 camelCase（边界 camelize）；请求体为 snake_case（pydantic 原生字段）

/** 智能体档案（AgentProfileListVO：camelCase） */
export interface AgentProfile {
  id: string;
  name: string;
  description?: string | null;
  avatar?: string | null;
  builtin?: boolean | null;
  active?: boolean | null;
  effectiveSlots?: number | null;
  inactiveSlots?: number | null;
  createTime?: string | null;
  updateTime?: string | null;
}

/** 档案列表响应（list() 返回 mode + 槽位总数 + agents） */
export interface AgentListResponse {
  mode: string;
  effectiveSlotTotal: number;
  agents: AgentProfile[];
}

/** 槽位提示词配置（AgentPromptConfigVO：camelCase） */
export interface AgentPromptSlotConfig {
  slotKey: string;
  displayName: string;
  group: string;
  groupName: string;
  effective: boolean;
  inactiveReason?: string | null;
  requiredPlaceholders: string[];
  content: string;
}

/** 槽位配置视图（loadPrompts 返回） */
export interface AgentPromptsView {
  agentId: string;
  agentName: string;
  builtin: boolean;
  defaultAgentName?: string | null;
  mode: string;
  slots: AgentPromptSlotConfig[];
}

/** 创建/更新档案载荷（snake_case；name 必填） */
export interface AgentProfilePayload {
  name: string;
  description?: string | null;
  avatar?: string | null;
}
