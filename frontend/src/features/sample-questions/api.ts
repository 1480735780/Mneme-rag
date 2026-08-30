// M4B T4 示例问题 REST API（对齐 rag/controller/sample_question_controller.py）
// - GET/POST /sample-questions；GET/PUT/DELETE /sample-questions/{id}；分页参数 current/size/keyword
import { del, get, post, put } from "@/shared/api/client";

import type { SampleQuestion, SampleQuestionPage, SampleQuestionPayload } from "./types";

/** GET /sample-questions：分页查询（keyword 对标题/问题模糊） */
export function getSampleQuestionsPage(
  current = 1,
  size = 10,
  keyword?: string,
): Promise<SampleQuestionPage> {
  return get("/sample-questions", { params: { current, size, keyword: keyword || undefined } });
}

/** GET /sample-questions/{id}：详情 */
export function getSampleQuestion(id: string): Promise<SampleQuestion> {
  return get(`/sample-questions/${encodeURIComponent(id)}`);
}

/** POST /sample-questions：创建，返回新 id */
export function createSampleQuestion(payload: SampleQuestionPayload): Promise<string> {
  return post("/sample-questions", payload);
}

/** PUT /sample-questions/{id}：更新 */
export function updateSampleQuestion(id: string, payload: SampleQuestionPayload): Promise<void> {
  return put(`/sample-questions/${encodeURIComponent(id)}`, payload);
}

/** DELETE /sample-questions/{id}：删除（软删） */
export function deleteSampleQuestion(id: string): Promise<void> {
  return del(`/sample-questions/${encodeURIComponent(id)}`);
}

/** GET /rag/sample-questions：随机示例问题（欢迎页，deleted=0 随机 3 条） */
export function listRandomSampleQuestions(): Promise<SampleQuestion[]> {
  return get("/rag/sample-questions");
}
