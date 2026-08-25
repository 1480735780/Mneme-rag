// M2/M3 系统设置 API（对齐 settings_controller：GET /rag/settings）
// M2 消费 ai.embedding.candidates（创建知识库 Embedding 下拉）；M3 Settings 只读页消费全字段
import { get } from "./client";

/** 模型候选（ModelCandidateVO：id/provider/model/url/dimension/priority/enabled/supportsThinking） */
export interface ModelCandidate {
  id: string;
  provider?: string | null;
  model?: string | null;
  url?: string | null;
  dimension?: number | null;
  priority?: number | null;
  enabled?: boolean | null;
  supportsThinking?: boolean | null;
}

/** 模型档位（tier 名 → 候选列表 + 超时） */
export interface ModelTier {
  candidates?: string[] | null;
  timeoutMs?: number | null;
}

/** 模型组（candidates + 档位；deepThinkingTier 为深度思考档） */
export interface ModelGroup {
  defaultModel?: string | null;
  candidates?: ModelCandidate[] | null;
  defaultTier?: string | null;
  deepThinkingTier?: string | null;
  tiers?: Record<string, ModelTier> | null;
}

/** RAG 默认检索配置 */
export interface RagDefaultConfig {
  collectionName?: string | null;
  dimension?: number | null;
  metricType?: string | null;
}

/** RAG 记忆配置 */
export interface RagMemoryConfig {
  historyKeepTurns?: number | null;
  summaryEnabled?: boolean | null;
  summaryStartTurns?: number | null;
  summaryMaxChars?: number | null;
  titleMaxLength?: number | null;
}

/** 全局限流配置（未装配时整体为 null） */
export interface RateLimitGlobal {
  enabled?: boolean | null;
  maxConcurrent?: number | null;
  maxWaitSeconds?: number | null;
  leaseSeconds?: number | null;
  pollIntervalMs?: number | null;
}

/** AI 提供商（apiKey 后端已脱敏） */
export interface AiProvider {
  url?: string | null;
  apiKey?: string | null;
  endpoints?: Record<string, string> | null;
}

/** 系统设置（SystemSettingsVO 完整投影，camelCase） */
export interface SystemSettings {
  orchestrationMode?: string | null;
  upload?: {
    maxFileSize?: number | null;
    maxRequestSize?: number | null;
  } | null;
  rag?: {
    default?: RagDefaultConfig | null;
    queryRewrite?: { enabled?: boolean | null } | null;
    citation?: { enabled?: boolean | null } | null;
    rateLimit?: { global?: RateLimitGlobal | null } | null;
    memory?: RagMemoryConfig | null;
  } | null;
  ai?: {
    providers?: Record<string, AiProvider> | null;
    chat?: ModelGroup | null;
    embedding?: ModelGroup | null;
    rerank?: ModelGroup | null;
    selection?: { failureThreshold?: number | null; openDurationMs?: number | null } | null;
    stream?: { messageChunkSize?: number | null } | null;
  } | null;
}

/** GET /rag/settings：系统设置聚合 */
export function getSystemSettings(): Promise<SystemSettings> {
  return get("/rag/settings");
}
