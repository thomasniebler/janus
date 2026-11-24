import { createContext, useContext } from 'react';

import type { PlanStepRecord } from '../lib/planSteps';
import type { ToolCallRecord } from '../types';

/** A chat approval answer; `always` also switches the tool to Always allow in chat (JAN-359). */
export type ToolApprovalAnswer = NonNullable<ToolCallRecord['approvalAnswer']>;

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  /** Tool calls of an assistant message, in call order (JAN-249). */
  toolCalls?: ToolCallRecord[];
  /** Raw UI blocks of an assistant message, in render order (JAN-252); validated when they render. */
  uiBlocks?: unknown[];
  /** Live only: the steps of the turn that produced this reply (JAN-316); never stored or reloaded. */
  planSteps?: PlanStepRecord[];
}

// Backend runtime-adapter tool-lifecycle event (JAN-163/JAN-167): the plan-driven
// runtime authorizes tools fresh per message and streams the resulting decision
// before __START__. This is display-only state, never appended to message text.
export interface ToolSelectionEvent {
  task_id: string;
  step_id: string;
  tools: Array<{ id: string; confirmation: 'never' | 'recommended' | 'required' }>;
  denied: Array<{ tool_id: string; reason: string }>;
  /** The brain-selected toolset of this message and why (JAN-300); absent or null when no selection ran. */
  toolset?: ToolsetSelection | null;
}

/** Which authorized toolboxes a message was offered (JAN-300). `tool_ids` null = all of them. */
export interface ToolsetSelection {
  source: 'selected' | 'persisted' | 'fallback' | 'unnarrowed';
  reason: string;
  tool_ids: string[] | null;
  trimmed: string[];
  widened: string[];
  authorized_count: number;
  narrowed: boolean;
}

/**
 * The tab-wide chat session (JAN-248). One `ChatSessionProvider` owns the only
 * `/ws/chat` socket in the tab; every chat view reads and drives it through
 * `useChatSession()`, so navigating, switching shells, or remounting a view never
 * loses the transcript or the draft.
 */
export interface ChatSessionValue {
  messages: ChatMessage[];
  draft: string;
  setDraft: (value: string) => void;
  isChatting: boolean;
  activeToolCalls: string[];
  toolSelection: ToolSelectionEvent | null;
  connectionError: string | null;
  activeConversationId: number | null;
  /** True while a restored conversation's messages are loading on mount. */
  isRestoring: boolean;
  /** The conversation whose delete request is in flight, if any. */
  deletingConversationId: number | null;
  /**
   * Sends the current draft with the current page's snapshot as `page_context`
   * (JAN-250); returns false when there is nothing to send or no open socket.
   */
  sendMessage: () => boolean;
  /**
   * Sends `text` as a user message the same way, without touching the draft (UI-block
   * action buttons, JAN-252); returns false when there is nothing to send or no open socket.
   */
  sendText: (text: string) => boolean;
  newConversation: () => Promise<void>;
  selectConversation: (conversationId: number) => Promise<void>;
  deleteConversation: (conversationId: number) => void;
  reconnect: () => void;
  /**
   * Answers one paused confirmation-required tool call (JAN-254). The decision
   * covers only that `callId`; returns false when there is no open socket.
   */
  answerToolApproval: (callId: string, decision: ToolApprovalAnswer) => boolean;
}

export const ChatSessionContext = createContext<ChatSessionValue | null>(null);

export const useChatSession = (): ChatSessionValue => {
  const value = useContext(ChatSessionContext);
  if (!value) {
    throw new Error('useChatSession must be used inside a ChatSessionProvider');
  }
  return value;
};

/** sessionStorage key holding a user's active conversation id for this tab. */
export const activeConversationStorageKey = (userId: number | string) =>
  `janus.chat.activeConversationId.${userId}`;
