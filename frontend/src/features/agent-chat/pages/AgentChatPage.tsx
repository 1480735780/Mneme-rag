// v1.1 P2 Agent 对话主页：URL 路由 ↔ currentSessionId 双向同步 + 会话加载/新建语义
// （移植 ragent-new AgentChatPage，路由换为 /agent、/agent/:conversationId，workflow /chat 保留）
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AgentChatInput from "../components/AgentChatInput";
import AgentLayout from "../components/AgentLayout";
import AgentMessageList from "../components/AgentMessageList";
import { useAgentChatStore } from "../store";

export default function AgentChatPage() {
  const navigate = useNavigate();
  const { conversationId } = useParams();
  const currentSessionId = useAgentChatStore((s) => s.currentSessionId);
  const sessions = useAgentChatStore((s) => s.sessions);
  const isCreatingNew = useAgentChatStore((s) => s.isCreatingNew);
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
  const loadMessages = useAgentChatStore((s) => s.loadMessages);
  const startNewChat = useAgentChatStore((s) => s.startNewChat);
  const [sessionsReady, setSessionsReady] = useState(false);

  const sessionExists = useMemo(() => {
    if (!conversationId) return false;
    return sessions.some((session) => session.id === conversationId);
  }, [conversationId, sessions]);

  useEffect(() => {
    let active = true;
    void loadSessions().finally(() => {
      if (active) setSessionsReady(true);
    });
    return () => {
      active = false;
    };
  }, [loadSessions]);

  useEffect(() => {
    if (conversationId) {
      if (sessionsReady && !sessionExists) {
        startNewChat();
        navigate("/agent", { replace: true });
        return;
      }
      void loadMessages(conversationId);
      return;
    }
    if (!sessionsReady) return;
    if (isCreatingNew) return;
    if (currentSessionId) return;
    startNewChat();
  }, [
    conversationId,
    sessionsReady,
    sessionExists,
    isCreatingNew,
    currentSessionId,
    loadMessages,
    startNewChat,
    navigate,
  ]);

  // 新会话在 meta 事件产生 conversationId 后同步 URL
  useEffect(() => {
    if (currentSessionId && currentSessionId !== conversationId) {
      navigate(`/agent/${currentSessionId}`, { replace: true });
    }
  }, [currentSessionId, conversationId, navigate]);

  return (
    <AgentLayout>
      <AgentMessageList />
      <AgentChatInput />
    </AgentLayout>
  );
}
