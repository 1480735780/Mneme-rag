// M3 Trace REST API（对齐 rag/controller/trace_controller.py）
// 统一经 Axios interceptor 解包；响应为 camelCase VO（后端边界 camelize）
import { get } from "@/shared/api/client";

import type { TraceDetail, TraceNode, TraceRunPage, TraceRunPageParams } from "./types";

/** GET /rag/traces/runs：分页查询运行记录（start_time 倒序 + 可选过滤） */
export function getTraceRunsPage(params: TraceRunPageParams = {}): Promise<TraceRunPage> {
  return get("/rag/traces/runs", {
    params: {
      current: params.current ?? 1,
      size: params.size ?? 10,
      traceId: params.traceId || undefined,
      conversationId: params.conversationId || undefined,
      taskId: params.taskId || undefined,
      status: params.status || undefined,
    },
  });
}

/** GET /rag/traces/runs/{traceId}：详情（含 nodes）；不存在返回 null data */
export function getTraceDetail(traceId: string): Promise<TraceDetail | null> {
  return get(`/rag/traces/runs/${encodeURIComponent(traceId)}`);
}

/** GET /rag/traces/runs/{traceId}/nodes：节点列表 */
export function getTraceNodes(traceId: string): Promise<TraceNode[]> {
  return get(`/rag/traces/runs/${encodeURIComponent(traceId)}/nodes`);
}
