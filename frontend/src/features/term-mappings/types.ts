// M4B T5 术语映射域类型（对齐 query_term_mapping_admin_service._to_vo + query_term_mapping_controller）
// - 响应为 camelCase（边界 camelize）；请求体为 snake_case（pydantic 原生字段）

/** 术语映射（QueryTermMappingVO：camelCase） */
export interface TermMapping {
  id?: string | null;
  sourceTerm?: string | null;
  targetTerm?: string | null;
  matchType?: number | null;
  priority?: number | null;
  enabled?: boolean | null;
  remark?: string | null;
  createTime?: string | null;
  updateTime?: string | null;
}

/** 创建/更新载荷（snake_case 请求体；source_term/target_term 创建必填） */
export interface TermMappingPayload {
  source_term?: string | null;
  target_term?: string | null;
  match_type?: number | null;
  priority?: number | null;
  enabled?: boolean | null;
  remark?: string | null;
}

/** 术语映射分页响应（对齐 query_term_mapping_admin_service.page_query：无 pages 字段） */
export interface TermMappingPage {
  records: TermMapping[];
  total: number;
  current: number;
  size: number;
}
