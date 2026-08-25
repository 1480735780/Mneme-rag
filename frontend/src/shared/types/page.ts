// 通用分页响应类型（对齐后端通用 page dict）
// 从 features/knowledge/types.ts 提升到 shared，供 trace/dashboard 等跨 feature 复用
export interface PageResult<T> {
  records: T[];
  total: number;
  size: number;
  current: number;
  pages: number;
}
