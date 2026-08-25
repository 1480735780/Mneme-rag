// M4C T8 摄取域类型（对齐 ingestion/controller/pipeline.py + task.py + service VO）
// - 响应为 camelCase；分页参数用 pageNo/pageSize（区别于其他模块 current/size）

/** 流水线节点类型（对齐 IngestionNodeType） */
export const PIPELINE_NODE_TYPES = [
  { value: "fetcher", label: "拉取" },
  { value: "parser", label: "解析" },
  { value: "enhancer", label: "增强" },
  { value: "chunker", label: "分块" },
  { value: "enricher", label: "富集" },
  { value: "indexer", label: "索引" },
] as const;

/** 任务状态（对齐 IngestionStatus） */
export const TASK_STATUSES = [
  { value: "pending", label: "排队中" },
  { value: "running", label: "执行中" },
  { value: "failed", label: "失败" },
  { value: "completed", label: "完成" },
] as const;

/** 文档源类型（对齐 SourceType） */
export const SOURCE_TYPES = [
  { value: "file", label: "文件" },
  { value: "url", label: "链接" },
  { value: "feishu", label: "飞书" },
] as const;

/** 流水线节点（IngestionPipelineNodeVO：camelCase） */
export interface PipelineNode {
  nodeId: string;
  nodeType: string;
  nextNodeId?: string | null;
  settings?: Record<string, unknown> | null;
  condition?: Record<string, unknown> | null;
}

/** 流水线（IngestionPipelineVO：camelCase） */
export interface Pipeline {
  id: string;
  name: string;
  description?: string | null;
  nodes: PipelineNode[];
  createdBy?: string | null;
  createTime?: string | null;
  updateTime?: string | null;
}

/** 创建/更新流水线载荷（camelCase 请求体，对齐 reqvo.py 原生字段） */
export interface PipelinePayload {
  name?: string;
  description?: string | null;
  nodes?: PipelineNode[];
}

/** 摄取任务（IngestionTaskVO：camelCase） */
export interface IngestionTask {
  id: string;
  pipelineId: string;
  sourceType?: string | null;
  sourceLocation?: string | null;
  sourceFileName?: string | null;
  status?: string | null;
  chunkCount?: number | null;
  errorMessage?: string | null;
  logs?: string[] | null;
  metadata?: Record<string, unknown> | null;
  startedAt?: string | null;
  completedAt?: string | null;
  createdBy?: string | null;
  createTime?: string | null;
  updateTime?: string | null;
}

/** 任务节点运行记录（IngestionTaskNodeVO：camelCase） */
export interface TaskNode {
  id: string;
  taskId: string;
  pipelineId?: string | null;
  nodeId?: string | null;
  nodeType?: string | null;
  nodeOrder?: number | null;
  status?: string | null;
  durationMs?: number | null;
  message?: string | null;
  errorMessage?: string | null;
  output?: Record<string, unknown> | null;
  createTime?: string | null;
  updateTime?: string | null;
}

/** 任务执行结果（POST /ingestion/tasks 返回） */
export interface TaskResultPayload {
  taskId: string;
  pipelineId: string;
  status?: string | null;
  chunkCount?: number | null;
  message?: string | null;
}

/** 摄取分页响应（对齐 pipeline/task service page：无 pages 字段） */
export interface IngestionPage<T> {
  records: T[];
  total: number;
  current: number;
  size: number;
}
