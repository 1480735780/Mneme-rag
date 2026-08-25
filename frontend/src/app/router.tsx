// M0 #5 路由定义
// - 公开路由：/login
// - 受保护路由：/（RequireAuth 包裹，AppLayout 布局）
// - Chat：/chat、/chat/:conversationId；admin：/admin/*（RequireAdmin 包裹）
// - M2 知识库：/admin/knowledge；M3：/admin/dashboard、/admin/traces、/admin/settings
// - M4 平台管理：/admin/users、/admin/change-logs、/admin/sample-questions、/admin/mappings、
//   /admin/intent-tree、/admin/agents、/admin/ingestion、/admin/graph、/admin/agent-debug
// - 兜底：* → NotFound
// 页面路由级懒加载（方案 §12 性能），Suspense 兜底用 Loading 三态
import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import AppLayout from "@/app/layout/AppLayout";
import { RequireAdmin, RequireAuth } from "@/features/auth/guards";
import { Loading } from "@/shared/components/AsyncState";

const LoginPage = lazy(() => import("@/features/auth/pages/LoginPage"));
const ChatPage = lazy(() => import("@/features/chat/pages/ChatPage"));
const KnowledgeListPage = lazy(() => import("@/features/knowledge/pages/KnowledgeListPage"));
const KnowledgeDocumentsPage = lazy(() => import("@/features/knowledge/pages/KnowledgeDocumentsPage"));
const KnowledgeChunksPage = lazy(() => import("@/features/knowledge/pages/KnowledgeChunksPage"));
const KnowledgeChunkLogsPage = lazy(() => import("@/features/knowledge/pages/KnowledgeChunkLogsPage"));
const KnowledgeDocumentPreviewPage = lazy(() => import("@/features/knowledge/pages/KnowledgeDocumentPreviewPage"));
const DashboardPage = lazy(() => import("@/features/dashboard/pages/DashboardPage"));
const TraceListPage = lazy(() => import("@/features/trace/pages/TraceListPage"));
const TraceDetailPage = lazy(() => import("@/features/trace/pages/TraceDetailPage"));
const SystemSettingsPage = lazy(() => import("@/features/settings/pages/SystemSettingsPage"));
const UserListPage = lazy(() => import("@/features/users/pages/UserListPage"));
const ChangeLogPage = lazy(() => import("@/features/change-logs/pages/ChangeLogPage"));
const SampleQuestionPage = lazy(() => import("@/features/sample-questions/pages/SampleQuestionPage"));
const TermMappingPage = lazy(() => import("@/features/term-mappings/pages/TermMappingPage"));
const IntentTreePage = lazy(() => import("@/features/intent-tree/pages/IntentTreePage"));
const AgentListPage = lazy(() => import("@/features/agents/pages/AgentListPage"));
const IngestionPage = lazy(() => import("@/features/ingestion/pages/IngestionPage"));
const GraphPage = lazy(() => import("@/features/graph/pages/GraphPage"));
const AgentDebugPage = lazy(() => import("@/features/agent-debug/pages/AgentDebugPage"));
const NotFoundPage = lazy(() => import("@/pages/NotFoundPage"));

export default function AppRouter() {
  return (
    <Suspense fallback={<Loading label="页面加载中…" />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Navigate to="/chat" replace />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:conversationId" element={<ChatPage />} />
            <Route path="/admin" element={<RequireAdmin />}>
              <Route index element={<Navigate to="/admin/knowledge" replace />} />
              <Route path="dashboard" element={<DashboardPage />} />
              <Route path="knowledge" element={<KnowledgeListPage />} />
              <Route path="knowledge/:kbId/documents" element={<KnowledgeDocumentsPage />} />
              <Route path="knowledge/:kbId/documents/:docId/chunks" element={<KnowledgeChunksPage />} />
              <Route path="knowledge/:kbId/documents/:docId/logs" element={<KnowledgeChunkLogsPage />} />
              <Route path="knowledge/:kbId/documents/:docId/preview" element={<KnowledgeDocumentPreviewPage />} />
              <Route path="traces" element={<TraceListPage />} />
              <Route path="traces/:traceId" element={<TraceDetailPage />} />
              <Route path="settings" element={<SystemSettingsPage />} />
              <Route path="users" element={<UserListPage />} />
              <Route path="change-logs" element={<ChangeLogPage />} />
              <Route path="sample-questions" element={<SampleQuestionPage />} />
              <Route path="mappings" element={<TermMappingPage />} />
              <Route path="intent-tree" element={<IntentTreePage />} />
              <Route path="agents" element={<AgentListPage />} />
              <Route path="ingestion" element={<IngestionPage />} />
              <Route path="graph" element={<GraphPage />} />
              <Route path="agent-debug" element={<AgentDebugPage />} />
            </Route>
          </Route>
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  );
}
