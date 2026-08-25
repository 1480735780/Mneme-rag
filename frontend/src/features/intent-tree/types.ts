// M4B T6 意图树域类型（对齐 intent_tree_admin_service._to_node_vo + intent_tree_controller）
// - 响应为 camelCase（边界 camelize）；请求体为 snake_case（pydantic 原生字段）

/** 意图层级（对齐 IntentLevel） */
export const INTENT_LEVELS = [
  { value: 0, label: "领域" },
  { value: 1, label: "类目" },
  { value: 2, label: "主题" },
] as const;

/** 意图类型（对齐 IntentKind） */
export const INTENT_KINDS = [
  { value: 0, label: "知识库" },
  { value: 1, label: "系统" },
  { value: 2, label: "MCP" },
] as const;

/** 意图节点（IntentNodeTreeVO：camelCase，children 递归嵌套） */
export interface IntentNode {
  id: string;
  kbId?: string | null;
  intentCode?: string | null;
  name?: string | null;
  level?: number | null;
  parentCode?: string | null;
  description?: string | null;
  collectionName?: string | null;
  collectionNames?: string[] | null;
  mcpToolId?: string | null;
  topK?: number | null;
  kind?: number | null;
  sortOrder?: number | null;
  promptSnippet?: string | null;
  promptTemplate?: string | null;
  paramPromptTemplate?: string | null;
  enabled?: boolean | null;
  examples?: string[] | null;
  children?: IntentNode[];
}

/** 创建/更新载荷（snake_case 请求体；intentCode/name 创建必填） */
export interface IntentNodePayload {
  intentCode?: string;
  name?: string;
  level?: number | null;
  kind?: number | null;
  parentCode?: string | null;
  description?: string | null;
  collectionNames?: string[] | null;
  mcpToolId?: string | null;
  examples?: string[] | null;
  topK?: number | null;
  sortOrder?: number | null;
  enabled?: number | null; // 请求体为 0/1（对齐 IntentNodeCreateRequest.enabled: int）
  paramPromptTemplate?: string | null;
  promptSnippet?: string | null;
  promptTemplate?: string | null;
}
