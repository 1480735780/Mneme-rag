// M2 知识域类型（对齐后端 knowledge 控制器 camelCase VO 输出）
// - KnowledgeBaseVO / KnowledgeDocumentVO / KnowledgeChunkVO / KnowledgeDocumentChunkLogVO
// - IngestionSpecSchemaVO / PageResult（自 shared/types/page 提升，此处 re-export 保持兼容）
export type { PageResult } from "@/shared/types/page";

/** 知识库（KnowledgeBaseVO：id/name/embeddingModel/collectionName/createdBy/createTime/updateTime/documentCount） */
export interface KnowledgeBase {
  id: string;
  name: string;
  embeddingModel?: string | null;
  collectionName: string;
  createdBy?: string | null;
  createTime?: string | null;
  updateTime?: string | null;
  documentCount?: number | null;
}

/** 创建知识库请求（camelCase，对齐 KnowledgeBaseCreateRequest） */
export interface KnowledgeBaseCreatePayload {
  name: string;
  embeddingModel?: string | null;
  collectionName: string;
}

/** 重命名知识库请求 */
export interface KnowledgeBaseUpdatePayload {
  name?: string;
  embeddingModel?: string | null;
}

/** 文档（KnowledgeDocumentVO） */
export interface KnowledgeDocument {
  id: string;
  kbId: string;
  docName: string;
  sourceType?: string | null;
  sourceLocation?: string | null;
  scheduleEnabled?: number | null;
  scheduleCron?: string | null;
  enabled?: boolean | null;
  chunkCount?: number | null;
  fileUrl?: string | null;
  fileType?: string | null;
  fileSize?: number | null;
  processMode?: string | null;
  ingestionSpec?: string | null;
  pipelineId?: string | null;
  status?: string | null;
  createdBy?: string | null;
  updatedBy?: string | null;
  createTime?: string | null;
  updateTime?: string | null;
  chunksEdited?: boolean | null;
  kbName?: string | null;
}

/** 文档分页查询参数 */
export interface KnowledgeDocumentPageParams {
  current?: number;
  size?: number;
  status?: string;
  keyword?: string;
}

/** 文档搜索项（KnowledgeDocumentSearchVO：仅 id/kbId/docName/kbName） */
export interface KnowledgeDocumentSearchItem {
  id: string;
  kbId: string;
  docName: string;
  kbName?: string | null;
}

/** 上传文档表单载荷（multipart 字段名对齐后端约定） */
export interface KnowledgeDocumentUploadPayload {
  sourceType: "file" | "url";
  file?: File | null;
  sourceLocation?: string | null;
  scheduleEnabled?: boolean;
  scheduleCron?: string | null;
  processMode?: "chunk" | "pipeline";
  ingestionSpec?: string | null;
  pipelineId?: string | null;
}

/** 更新文档请求（camelCase，对齐 KnowledgeDocumentUpdateRequest） */
export interface KnowledgeDocumentUpdatePayload {
  docName?: string;
  processMode?: string;
  ingestionSpec?: string;
  pipelineId?: string;
  sourceLocation?: string;
  scheduleEnabled?: number;
  scheduleCron?: string;
}

/** 分块日志（KnowledgeDocumentChunkLogVO） */
export interface KnowledgeDocumentChunkLog {
  id: string;
  docId: string;
  status: string;
  processMode?: string | null;
  parseProfile?: string | null;
  pipelineId?: string | null;
  pipelineName?: string | null;
  extractDuration?: number | null;
  chunkDuration?: number | null;
  embedDuration?: number | null;
  persistDuration?: number | null;
  otherDuration?: number | null;
  totalDuration?: number | null;
  chunkCount?: number | null;
  errorMessage?: string | null;
  startTime?: string | null;
  endTime?: string | null;
  createTime?: string | null;
}

/** 文档块（KnowledgeChunkVO） */
export interface KnowledgeChunk {
  id: string;
  kbId?: string | null;
  docId: string;
  chunkIndex?: number | null;
  content?: string | null;
  contentHash?: string | null;
  charCount?: number | null;
  tokenCount?: number | null;
  enabled?: number | null;
  createTime?: string | null;
  updateTime?: string | null;
}

/** Chunk 分页查询参数（enabled 为 0/1 数字） */
export interface KnowledgeChunkPageParams {
  current?: number;
  size?: number;
  enabled?: number;
}

/** 档位选项（parseProfiles） */
export interface ParseProfileOption {
  value: string;
  label: string;
  hint?: string | null;
}

/** 预算字段定义（budgetFields） */
export interface BudgetFieldSchema {
  key: string;
  label: string;
  defaultValue: number;
  min: number;
  max: number;
  recommendedMin: number;
  recommendedMax: number;
  hint?: string | null;
  detail?: string | null;
}

/** 摄取配置表单 schema（IngestionSpecSchemaVO：后端下发字段定义与取值范围） */
export interface IngestionSpecSchema {
  parseProfileLabel: string;
  parseProfiles: ParseProfileOption[];
  parseProfileExtensions: string[];
  budgetFields: BudgetFieldSchema[];
  wholeDocumentSentinel: number;
}
