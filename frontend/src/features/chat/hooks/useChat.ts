// M1 #5 聊天 hooks：消息 / 流式状态 / 发送 / 停止
import { useChatStore } from "../store";

export function useChat() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const error = useChatStore((s) => s.error);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const stopGeneration = useChatStore((s) => s.stopGeneration);

  return { messages, isStreaming, error, sendMessage, stopGeneration };
}
