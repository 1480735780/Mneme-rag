// M4C T8 任务状态展示映射（对齐 IngestionStatus：pending/running/failed/completed）
import { TASK_STATUSES } from "./types";

export function taskStatusMeta(status?: string | null) {
  return (
    TASK_STATUSES.find((s) => s.value === status) ?? { value: status ?? "", label: status ?? "未知" }
  );
}
