import { closeOpenSteps, parsePlanEvent, PLAN_EVENT_PREFIX, upsertPlanStep } from '../lib/planSteps';
import {
  attachApprovalRequest,
  dropPendingApprovals,
  parseToolApproval,
  TOOL_APPROVAL_PREFIX,
  toToolCallRecords,
} from '../lib/toolCallEvents';
import { parseUIBlockFrame, toUIBlockPayloads, UI_BLOCK_PREFIX } from '../lib/uiBlocks';
import type { ChatMessage, ToolSelectionEvent } from './chatSession';

/** Keeps long sessions bounded (see the tech spec's "Resilient chat UI"). */
export const MAX_RETAINED_MESSAGES = 50;

type MessageUpdater = (updater: (previous: ChatMessage[]) => ChatMessage[]) => void;

/** Returns `messages` with `update` applied to the streaming (last) assistant message; unchanged without one. */
export const updateLastAssistant = (
  messages: ChatMessage[],
  update: (message: ChatMessage) => ChatMessage
): ChatMessage[] => {
  const last = messages[messages.length - 1];
  if (!last || last.role !== 'assistant') return messages;
  return [...messages.slice(0, -1), update(last)];
};

/**
 * Applies a `/ws/chat` frame that only adds to the streaming assistant message: a
 * `__TOOL_APPROVAL__` request put on its call (JAN-254), a `__UI_BLOCK__` appended
 * to its `uiBlocks` (JAN-252; validated when it renders), or a `__PLAN_EVENT__`
 * merged into its live `planSteps` (JAN-316). Returns whether it was one.
 */
export const applyAssistantFrame = (message: string, appendMessages: MessageUpdater): boolean => {
  if (message.startsWith(PLAN_EVENT_PREFIX)) {
    const step = parsePlanEvent(message.slice(PLAN_EVENT_PREFIX.length));
    if (step) {
      appendMessages((prev) => updateLastAssistant(prev, (last) => ({ ...last, planSteps: upsertPlanStep(last.planSteps, step) })));
    }
    return true;
  }
  if (message.startsWith(TOOL_APPROVAL_PREFIX)) {
    const request = parseToolApproval(message.slice(TOOL_APPROVAL_PREFIX.length));
    if (request) {
      appendMessages((prev) =>
        updateLastAssistant(prev, (last) => ({ ...last, toolCalls: attachApprovalRequest(last.toolCalls, request) }))
      );
    }
    return true;
  }
  if (message.startsWith(UI_BLOCK_PREFIX)) {
    const block = parseUIBlockFrame(message.slice(UI_BLOCK_PREFIX.length));
    if (block !== undefined) {
      appendMessages((prev) => updateLastAssistant(prev, (last) => ({ ...last, uiBlocks: [...(last.uiBlocks ?? []), block] })));
    }
    return true;
  }
  return false;
};

/**
 * Returns `messages` after the socket closed mid-turn: every call still waiting
 * for approval is closed `disconnected` (JAN-254) and every unfinished turn step
 * `failed` (JAN-316). Unchanged when nothing was open.
 */
export const closeInterruptedTurn = (messages: ChatMessage[]): ChatMessage[] => {
  let changed = false;
  const next = messages.map((message) => {
    const toolCalls = dropPendingApprovals(message.toolCalls);
    const planSteps = closeOpenSteps(message.planSteps);
    if (toolCalls === message.toolCalls && planSteps === message.planSteps) return message;
    changed = true;
    return { ...message, toolCalls, planSteps };
  });
  return changed ? next : messages;
};

export const parseToolSelectionEvent = (raw: string): ToolSelectionEvent | null => {
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && Array.isArray(parsed.tools) && Array.isArray(parsed.denied)) {
      return parsed as ToolSelectionEvent;
    }
  } catch {
    // Malformed/unexpected payload: ignore rather than surface as chat text.
  }
  return null;
};

/** Messages of `GET /api/conversations/{id}` with their stored tool calls (JAN-249) and UI blocks (JAN-252). */
export const toChatMessages = (
  messages: Array<{ role: string; content: string; tool_calls?: unknown; ui_blocks?: unknown }>
): ChatMessage[] =>
  messages
    .map((m) => {
      const message: ChatMessage = { role: m.role as ChatMessage['role'], content: m.content };
      const toolCalls = toToolCallRecords(m.tool_calls);
      if (toolCalls.length > 0) message.toolCalls = toolCalls;
      const uiBlocks = toUIBlockPayloads(m.ui_blocks);
      if (uiBlocks.length > 0) message.uiBlocks = uiBlocks;
      return message;
    })
    .slice(-MAX_RETAINED_MESSAGES);
