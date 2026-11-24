import type { ToolCallRecord } from '../types';
import { isAwaitingApproval } from './toolCallEvents';

// The step model lives here rather than in `types.ts` (held at its size by
// eslint.baseline.json); a call's step link is added to `ToolCallRecord` below.
declare module '../types' {
  interface ToolCallRecord {
    /** Live only: the turn step the call ran in (JAN-316); never stored, so absent on reloaded calls. */
    step_id?: string | null;
  }
}

/**
 * Status of one turn step on the wire (`__PLAN_EVENT__`, JAN-316): the backend's
 * `PlanStepStatus` values (`plan_graph.py`), shared with the Tasks/Plans surface (JAN-204).
 */
type PlanStepStatus = 'pending' | 'running' | 'completed' | 'failed';

/** How the timeline shows a step: the wire status, or `waiting` while one of its calls awaits approval. */
export type PlanStepDisplayStatus = PlanStepStatus | 'waiting';

/** One step of the current chat turn: a model round that asked for tools (JAN-316). Live only. */
export interface PlanStepRecord {
  /** Turn-local id (`step-1`, ...); the turn's own plan step id is `__TOOL_SELECTION__`'s `step_id`. */
  step_id: string;
  index: number;
  status: PlanStepStatus;
  /** The tools the model asked for in this round, in its order. */
  tool_names: string[];
}

/** Prefix of the `/ws/chat` turn-step frame (JAN-316). */
export const PLAN_EVENT_PREFIX = '__PLAN_EVENT__:';

const STATUSES: readonly PlanStepStatus[] = ['pending', 'running', 'completed', 'failed'];

const isStatus = (value: unknown): value is PlanStepStatus =>
  typeof value === 'string' && (STATUSES as readonly string[]).includes(value);

/** Whether a step reached its final status. */
const isTerminalStep = (step: PlanStepRecord): boolean => step.status === 'completed' || step.status === 'failed';

/** How the timeline labels each status; `pending` is a step the model generated that has not started. */
export const STEP_STATUS_LABELS: Record<PlanStepDisplayStatus, string> = {
  pending: 'generated',
  running: 'running',
  waiting: 'waiting for approval',
  completed: 'completed',
  failed: 'failed',
};

/** Parses a `__PLAN_EVENT__:` frame body; `null` for malformed payloads. */
export const parsePlanEvent = (body: string): PlanStepRecord | null => {
  try {
    const value = JSON.parse(body) as Record<string, unknown> | null;
    if (!value || typeof value !== 'object') return null;
    if (typeof value.step_id !== 'string' || !value.step_id || !isStatus(value.status)) return null;
    const index = typeof value.index === 'number' && Number.isFinite(value.index) ? value.index : 0;
    const toolNames = Array.isArray(value.tool_names)
      ? value.tool_names.filter((name): name is string => typeof name === 'string')
      : [];
    return { step_id: value.step_id, index, status: value.status, tool_names: toolNames };
  } catch {
    return null;
  }
};

/**
 * Returns `steps` with `event` merged in by `step_id` (a new step is appended).
 * A finished step never goes back to running: a late frame cannot reopen it.
 */
export const upsertPlanStep = (steps: PlanStepRecord[] | undefined, event: PlanStepRecord): PlanStepRecord[] => {
  const existing = steps ?? [];
  const index = existing.findIndex((step) => step.step_id === event.step_id);
  if (index === -1) return [...existing, event];
  const current = existing[index];
  if (isTerminalStep(current) && !isTerminalStep(event)) return existing;
  return existing.map((step, position) =>
    position === index ? { ...event, tool_names: event.tool_names.length ? event.tool_names : step.tool_names } : step
  );
};

/**
 * Returns `steps` with every unfinished step marked `failed`: the socket closed
 * mid-turn, so no closing frame will arrive. Unchanged when nothing was open.
 */
export const closeOpenSteps = (steps: PlanStepRecord[] | undefined): PlanStepRecord[] | undefined => {
  if (!steps?.some((step) => !isTerminalStep(step))) return steps;
  return steps.map((step) => (isTerminalStep(step) ? step : { ...step, status: 'failed' }));
};

export interface TimelineStep {
  step: PlanStepRecord;
  status: PlanStepDisplayStatus;
  calls: ToolCallRecord[];
}

/**
 * Groups a turn's calls under its steps, in step order. A call names its step
 * (`step_id`); one without (an approval request whose `start` frame has not
 * arrived yet) goes to the newest unfinished step, else the newest step. A step
 * shows `waiting` while it is unfinished and one of its calls awaits approval.
 * Calls that fit no step are returned as `ungrouped`.
 */
export const buildTimeline = (
  steps: PlanStepRecord[] | undefined,
  calls: ToolCallRecord[] | undefined
): { steps: TimelineStep[]; ungrouped: ToolCallRecord[] } => {
  const ordered = [...(steps ?? [])].sort((a, b) => a.index - b.index);
  const byId = new Map(ordered.map((step) => [step.step_id, [] as ToolCallRecord[]]));
  const fallback = [...ordered].reverse().find((step) => !isTerminalStep(step)) ?? ordered[ordered.length - 1];
  const ungrouped: ToolCallRecord[] = [];
  for (const call of calls ?? []) {
    const target = (call.step_id && byId.get(call.step_id)) || (!call.step_id && fallback ? byId.get(fallback.step_id) : undefined);
    if (target) target.push(call);
    else ungrouped.push(call);
  }
  return {
    steps: ordered.map((step) => {
      const stepCalls = byId.get(step.step_id) ?? [];
      const waiting = !isTerminalStep(step) && stepCalls.some(isAwaitingApproval);
      return { step, status: waiting ? 'waiting' : step.status, calls: stepCalls };
    }),
    ungrouped,
  };
};
