// M2 状态展示映射（对齐 knowledge/enums.DocumentStatus 与 ChunkVO.enabled）

/** 文档处理状态 → 徽标文案与色调（对齐后端 pending/running/failed/success） */
export const DOCUMENT_STATUS_META: Record<string, { label: string; tone: "default" | "secondary" | "destructive" | "outline" }> = {
  pending: { label: "待处理", tone: "secondary" },
  running: { label: "处理中", tone: "outline" },
  success: { label: "成功", tone: "default" },
  failed: { label: "失败", tone: "destructive" },
};

export function documentStatusMeta(status?: string | null) {
  return DOCUMENT_STATUS_META[status ?? ""] ?? { label: status ?? "未知", tone: "secondary" as const };
}

/** Chunk 启停（enabled 为 0/1 数字） */
export function chunkEnabledMeta(enabled?: number | null) {
  return enabled === 1
    ? { label: "启用", tone: "default" as const }
    : { label: "禁用", tone: "secondary" as const };
}
