// M4B T4 示例问题域类型（对齐 rag/controller/vo.py SampleQuestionVO + sample_question_controller）

/** 示例问题（SampleQuestionVO：camelCase） */
export interface SampleQuestion {
  id?: string | null;
  title?: string | null;
  description?: string | null;
  question?: string | null;
  createTime?: string | null;
  updateTime?: string | null;
}

/** 创建/更新载荷（POST/PUT /sample-questions：question 创建必填，其余可选） */
export interface SampleQuestionPayload {
  title?: string | null;
  description?: string | null;
  question?: string;
}

/** 示例问题分页响应（对齐 sample_question_service.page_query：无 pages 字段） */
export interface SampleQuestionPage {
  records: SampleQuestion[];
  total: number;
  current: number;
  size: number;
}
