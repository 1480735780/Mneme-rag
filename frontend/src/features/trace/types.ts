// M3 Trace 域类型（对齐 rag/service/trace_service.py 的 VO 投影 + trace_controller camelize）
// - RagTraceRunVO / RagTraceNodeVO / RagTraceDetailVO
// - 分页响应 {records,total,current,size}（无 pages 字段，区别于 knowledge PageResult）

/** 追踪运行（RagTraceRunVO：camelCase） */
export interface TraceRun {
  traceId: string;
  traceName?: string | null;
  entryMethod?: string | null;
  conversationId?: string | null;
  taskId?: string | null;
  userId?: string | null;
  username?: string | null;
  status: string;
  errorMessage?: string | null;
  durationMs?: number | null;
  ttftMs?: number | null;
  question?: string | null;
  startTime?: string | null;
  endTime?: string | null;
}

/** 追踪节点（RagTraceNodeVO：camelCase） */
export interface TraceNode {
  traceId: string;
  nodeId: string;
  parentNodeId?: string | null;
  depth?: number | null;
  nodeType?: string | null;
  nodeName?: string | null;
  className?: string | null;
  methodName?: string | null;
  status?: string | null;
  errorMessage?: string | null;
  durationMs?: number | null;
  startTime?: string | null;
  endTime?: string | null;
}

/** 追踪详情（RagTraceDetailVO：run + nodes）；traceId 不存在后端返回 null data */
export interface TraceDetail {
  run: TraceRun;
  nodes: TraceNode[];
}

/** 追踪分页响应（对齐 trace_service.page_runs：无 pages 字段） */
export interface TraceRunPage {
  records: TraceRun[];
  total: number;
  current: number;
  size: number;
}

/** 追踪分页/过滤参数（traceId/conversationId/taskId/status 可选过滤） */
export interface TraceRunPageParams {
  current?: number;
  size?: number;
  traceId?: string;
  conversationId?: string;
  taskId?: string;
  status?: string;
}
