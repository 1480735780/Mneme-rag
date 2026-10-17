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
  /** P10.2 T2.4：生效内容来源（pointer=版本指针 / slot=存量 content / unconfigured=未配置） */
  contentSource?: string | null;
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

/** ================ P10.2 T2.4 提示词版本管理（D8：保存≠生效，激活=显式动作） ================ */

/** 保存槽位提示词返回的版本摘要（D8：保存=新建不可变版本行，不生效） */
export interface AgentPromptSavedVersion {
  agentId: string;
  slotKey: string;
  versionId: string;
  versionNo: number | null;
}

/** 提示词版本行（版本历史条目） */
export interface PromptVersionItem {
  versionId: string;
  versionNo: number | null;
  content?: string | null;
  createBy?: string | null;
  createTime?: string | null;
  isActive: boolean;
}

/** 版本历史视图（GET /agents/{id}/prompts/{slotKey}/versions） */
export interface PromptVersionList {
  agentId: string;
  slotKey: string;
  /** 当前生效来源：pointer / slot / unconfigured */
  activeSource: string;
  activeVersionId?: string | null;
  versions: PromptVersionItem[];
}

/** 版本当前生效侧（diff 预览的 from） */
export interface PromptVersionCurrent {
  source: string;
  versionId?: string | null;
  versionNo?: number | null;
  content?: string | null;
}

/** diff 预览（GET …/versions/{versionId}/preview）：当前生效 vs 目标版本 unified diff */
export interface PromptVersionPreview {
  agentId: string;
  slotKey: string;
  slotDisplayName: string;
  current: PromptVersionCurrent;
  target: { versionId: string; versionNo?: number | null; content?: string | null };
  changed: boolean;
  unifiedDiff: string[];
}
