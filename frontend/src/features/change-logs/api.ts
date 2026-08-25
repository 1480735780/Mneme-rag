// M4A 审计日志 REST API（对齐 audit/controller/change_log_controller.py）
// - GET /biz-change-logs 分页（query 参数 camelCase alias：bizType/operationType/operatorId/success/beginTime/endTime）
// - GET /biz-change-logs/{id} 详情（不存在抛 ClientException）
import { get } from "@/shared/api/client";

import type { BizChangeLog, BizChangeLogPage, BizChangeLogPageParams } from "./types";

/** GET /biz-change-logs：审计日志分页（create_time 倒序 + 可选过滤） */
export function getChangeLogsPage(params: BizChangeLogPageParams = {}): Promise<BizChangeLogPage> {
  return get("/biz-change-logs", {
    params: {
      current: params.current ?? 1,
      size: params.size ?? 10,
      bizType: params.bizType || undefined,
      operationType: params.operationType || undefined,
      operatorId: params.operatorId || undefined,
      success: params.success === undefined ? undefined : String(params.success),
      beginTime: params.beginTime || undefined,
      endTime: params.endTime || undefined,
    },
  });
}

/** GET /biz-change-logs/{id}：按 id 查详情 */
export function getChangeLog(id: string): Promise<BizChangeLog> {
  return get(`/biz-change-logs/${encodeURIComponent(id)}`);
}
