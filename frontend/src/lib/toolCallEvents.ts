import type { ToolApprovalDecision, ToolApprovalRequest, ToolCallPhase, ToolCallRecord } from '../types';

/** Prefix of the structured `/ws/chat` tool-call frame (JAN-249). */
export const TOOL_EVENT_PREFIX = '__TOOL_EVENT__:';

/** Prefix of the `/ws/chat` approval request for a confirmation-required call (JAN-254). */
export const TOOL_APPROVAL_PREFIX = '__TOOL_APPROVAL__:';

/** Deprecated one-word markers the backend still streams for older clients; ignored here. */
export const LEGACY_TOOL_MARKER_PREFIXES = ['__TOOL_CALL__:', '__TOOL_RESULT__:'] as const;

const PHASES: readonly ToolCallPhase[] = ['start', 'end', 'error', 'blocked'];

const isPhase = (value: unknown): value is ToolCallPhase =>
  typeof value === 'string' && (PHASES as readonly string[]).includes(value);

const DECISIONS: readonly ToolApprovalDecision[] = ['approved', 'denied', 'timeout', 'disconnected', 'unavailable'];

const toDecision = (value: unknown): ToolApprovalDecision | null =>
  typeof value === 'string' && (DECISIONS as readonly string[]).includes(value) ? (value as ToolApprovalDecision) : null;

const optionalString = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const optionalNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/** Validates one tool-call payload (a frame body or a stored row); `null` when malformed. */
const toToolCallRecord = (raw: unknown): ToolCallRecord | null => {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.call_id !== 'string' || !value.call_id) return null;
  if (typeof value.tool_name !== 'string' || !isPhase(value.phase)) return null;
  return {
    call_id: value.call_id,
    tool_id: optionalString(value.tool_id),
    tool_name: value.tool_name,
    phase: value.phase,
    args: value.args ?? null,
    args_truncated: value.args_truncated === true,
    result_preview: optionalString(value.result_preview),
    result_truncated: value.result_truncated === true,
    duration_ms: optionalNumber(value.duration_ms),
    approval: toDecision(value.approval),
    ...(typeof value.step_id === 'string' && value.step_id ? { step_id: value.step_id } : {}),
  };
};

/** A call's (redacted) arguments as pretty-printed JSON; already-cut JSON text (a string) is shown as-is. */
export const formatToolArgs = (args: unknown): string => {
  if (args == null) return '(none)';
  if (typeof args === 'string') return args;
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
};

/** Parses a `__TOOL_EVENT__:` frame body; `null` for malformed payloads. */
export const parseToolEvent = (body: string): ToolCallRecord | null => {
  try {
    return toToolCallRecord(JSON.parse(body));
  } catch {
    return null;
  }
};

/**
 * Returns `calls` with `event` merged in by `call_id` (a new call is appended).
 * A live approval request on the call is kept while it runs; a finished call drops it.
 */
export const upsertToolCall = (calls: ToolCallRecord[] | undefined, event: ToolCallRecord): ToolCallRecord[] => {
  const existing = calls ?? [];
  const index = existing.findIndex((call) => call.call_id === event.call_id);
  if (index === -1) return [...existing, event];
  return existing.map((call, position) => {
    if (position !== index) return call;
    const merged: ToolCallRecord = { ...call, ...event, approval: event.approval ?? call.approval };
    if (event.phase !== 'start') {
      delete merged.approvalRequest;
      delete merged.approvalAnswer;
    }
    return merged;
  });
};

/** Parses a `__TOOL_APPROVAL__:` frame body; `null` for malformed payloads. */
export const parseToolApproval = (body: string): ToolApprovalRequest | null => {
  try {
    const value = JSON.parse(body) as Record<string, unknown> | null;
    if (!value || typeof value !== 'object') return null;
    if (typeof value.call_id !== 'string' || !value.call_id || typeof value.tool_name !== 'string') return null;
    return {
      call_id: value.call_id,
      tool_id: typeof value.tool_id === 'string' ? value.tool_id : '',
      tool_name: value.tool_name,
      display_name: typeof value.display_name === 'string' ? value.display_name : value.tool_name,
      risk_level: typeof value.risk_level === 'string' ? value.risk_level : '',
      reason: typeof value.reason === 'string' ? value.reason : '',
      args: value.args ?? null,
      args_truncated: value.args_truncated === true,
      timeout_seconds: optionalNumber(value.timeout_seconds) ?? 0,
    };
  } catch {
    return null;
  }
};

/** Returns `calls` with `request` put on its call (the call is added if its `start` event has not arrived). */
export const attachApprovalRequest = (
  calls: ToolCallRecord[] | undefined,
  request: ToolApprovalRequest
): ToolCallRecord[] => {
  const existing = calls ?? [];
  if (!existing.some((call) => call.call_id === request.call_id)) {
    return [
      ...existing,
      {
        call_id: request.call_id,
        tool_id: request.tool_id || null,
        tool_name: request.tool_name,
        phase: 'start',
        args: request.args,
        args_truncated: request.args_truncated,
        result_preview: null,
        result_truncated: false,
        duration_ms: null,
        approval: null,
        approvalRequest: request,
      },
    ];
  }
  return existing.map((call) => (call.call_id === request.call_id ? { ...call, approvalRequest: request } : call));
};

/** Whether a call is waiting for the user's Approve / Deny. */
export const isAwaitingApproval = (call: ToolCallRecord): boolean =>
  call.phase === 'start' && call.approvalRequest != null;

/**
 * Returns `calls` with every still-waiting approval closed as `disconnected`: the
 * socket is gone, so the backend has already refused those calls (JAN-254).
 */
export const dropPendingApprovals = (calls: ToolCallRecord[] | undefined): ToolCallRecord[] | undefined => {
  if (!calls?.some(isAwaitingApproval)) return calls;
  return calls.map((call) => {
    if (!isAwaitingApproval(call)) return call;
    const closed: ToolCallRecord = { ...call, phase: 'blocked', approval: 'disconnected' };
    delete closed.approvalRequest;
    delete closed.approvalAnswer;
    return closed;
  });
};

/** Converts stored `tool_calls` rows, dropping anything malformed. */
export const toToolCallRecords = (rows: unknown): ToolCallRecord[] =>
  Array.isArray(rows) ? rows.map(toToolCallRecord).filter((call): call is ToolCallRecord => call !== null) : [];
