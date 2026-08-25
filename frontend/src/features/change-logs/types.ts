// M4A 审计日志域类型（对齐 audit/controller/change_log_controller.py + t_biz_change_log）
// - 响应经 camelize 为 camelCase；分页含 hasMore 无 pages（区别于 knowledge PageResult）

/** 业务变更日志（BizChangeLogVO：camelCase） */
export interface BizChangeLog {
  id: string;
  bizType?: string | null;
  bizId?: string | null;
  operationType?: string | null;
  actionDesc?: string | null;
  beforeSnapshot?: string | null;
  afterSnapshot?: string | null;
  changeDiff?: string | null;
  operatorId?: string | null;
  operatorName?: string | null;
  operatorRole?: string | null;
  success?: boolean | null;
  errorMessage?: string | null;
  className?: string | null;
  methodName?: string | null;
  ip?: string | null;
  userAgent?: string | null;
  createTime?: string | null;
}

/** 审计日志分页/过滤参数（query 参数为 camelCase alias：bizType/operationType/operatorId/success/时间窗） */
export interface BizChangeLogPageParams {
  current?: number;
  size?: number;
  bizType?: string;
  operationType?: string;
  operatorId?: string;
  success?: boolean;
  beginTime?: string;
  endTime?: string;
}

/** 审计日志分页响应（对齐 change_log_query_service.page：含 hasMore，无 pages） */
export interface BizChangeLogPage {
  records: BizChangeLog[];
  total: number;
  current: number;
  size: number;
  hasMore: boolean;
}
