import api from '../api';
import { parseResponse } from './apiValidation';
import type { Parsed } from './apiValidation';
import { runtimeTaskDetailSchema, runtimeTaskPageSchema } from './runtimeSchemas';
import type { RuntimeTriggerStatus } from '../types';

/**
 * The Tasks page (JAN-204): persisted plan-runtime task state. A task is a
 * trigger run (ad hoc or from a saved definition) or a delegated child task;
 * its steps and tool trace come from the durable plan snapshot. Mirrors
 * `src/janus/schemas/runtime_tasks.py`.
 */

/** Where a task came from. */
export type RuntimeTaskSource = 'trigger' | 'definition' | 'delegated';

export const RUNTIME_TASK_SOURCES: readonly RuntimeTaskSource[] = ['trigger', 'definition', 'delegated'];

/** Labels for the source filter and badges. */
export const TASK_SOURCE_LABELS: Record<RuntimeTaskSource, string> = {
  trigger: 'Ad-hoc trigger run',
  definition: 'Automation run',
  delegated: 'Delegated task',
};

/** A link to another task: any trigger run, or a delegated task. */
export interface RuntimeTaskRef {
  kind: 'trigger' | 'delegated';
  ref_id: string;
}

export interface RuntimeTaskSummary {
  source: RuntimeTaskSource;
  /** `trigger_id` for trigger runs, `delegation_id` for delegated tasks. */
  ref_id: string;
  task_id: string;
  title: string;
  status: RuntimeTriggerStatus;
  created_at: string;
  completed_at: string | null;
  /** `PlanStatus` (`draft`, `active`, `waiting`, `completed`, `failed`); `null` before a plan exists. */
  plan_status: string | null;
  step_count: number;
  completed_step_count: number;
  failed_step_count: number;
  current_step: string | null;
  definition_id: number | null;
  definition_name: string | null;
  conversation_id: number | null;
  child_count: number;
}

export interface RuntimeTaskPage {
  items: RuntimeTaskSummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

/** The tool trace recorded for one executed step (the run's audit header). */
export interface RuntimeStepTrace {
  model: string | null;
  authorized_tools: string[];
  invoked_tools: string[];
  skipped: string | null;
  refused: string | null;
  failed: string | null;
  suspected_injection: string | null;
  /** Which authorized toolboxes the brain selected for the step, and why (JAN-300); absent on older runs. */
  tool_selection?: string | null;
}

export interface RuntimeTaskStep {
  step_id: string;
  objective: string;
  /** `PlanStepStatus`: `pending`, `running`, `completed` or `failed`. */
  status: string;
  depends_on: string[];
  required_tool_ids: string[];
  trace: RuntimeStepTrace | null;
  /** The model's answer for the step. Untrusted text: always rendered as plain text. */
  output: string | null;
  error: string | null;
}

export interface RuntimeTaskDetail extends RuntimeTaskSummary {
  objective: string | null;
  task_mode: string | null;
  node: string | null;
  reason: string | null;
  steps: RuntimeTaskStep[];
  trigger_source: string | null;
  attempt_count: number | null;
  last_error: string | null;
  allowed_tool_ids: string[];
  delegation_result: string | null;
  delegation_error: string | null;
  parent: RuntimeTaskRef | null;
  children: RuntimeTaskSummary[];
}

export interface RuntimeTaskQuery {
  source?: RuntimeTaskSource;
  status?: RuntimeTriggerStatus;
  limit?: number;
  offset?: number;
}

export const runtimeTaskApi = {
  list: (params?: RuntimeTaskQuery): Parsed<RuntimeTaskPage> =>
    parseResponse(runtimeTaskPageSchema)(api.get('/api/runtime/tasks/', { params })),
  detail: (ref: RuntimeTaskRef): Parsed<RuntimeTaskDetail> =>
    parseResponse(runtimeTaskDetailSchema)(
      api.get(`/api/runtime/tasks/${ref.kind === 'trigger' ? 'triggers' : 'delegations'}/${encodeURIComponent(ref.ref_id)}`)
    ),
};

/** Query keys the Agent Tasks components use; stories and tests seed the cache with them. */
export const taskListQueryKey = (query: RuntimeTaskQuery) => ['runtime-tasks', query] as const;
export const taskDetailQueryKey = (ref: RuntimeTaskRef) => ['runtime-task', ref.kind, ref.ref_id] as const;

/** The link kind of a listed task: definition runs are trigger runs too. */
export const taskRef = (task: Pick<RuntimeTaskSummary, 'source' | 'ref_id'>): RuntimeTaskRef => ({
  kind: task.source === 'delegated' ? 'delegated' : 'trigger',
  ref_id: task.ref_id,
});

/** Search parameters that select a task on the Tasks page. */
export const TASK_KIND_PARAM = 'kind';
export const TASK_ID_PARAM = 'task';

/** The Tasks page URL that opens `ref`. */
export const taskDetailPath = (ref: RuntimeTaskRef): string =>
  `/tasks?${new URLSearchParams({ [TASK_KIND_PARAM]: ref.kind, [TASK_ID_PARAM]: ref.ref_id }).toString()}`;

/** Reads the selected task from the page's search parameters. */
export const readTaskRef = (params: URLSearchParams): RuntimeTaskRef | null => {
  const kind = params.get(TASK_KIND_PARAM);
  const refId = params.get(TASK_ID_PARAM);
  if (!refId || (kind !== 'trigger' && kind !== 'delegated')) return null;
  return { kind, ref_id: refId };
};

/** Whether a task is still in motion (including a run waiting on its delegated children), so its view should keep polling. */
export const isActiveTask = (task: Pick<RuntimeTaskSummary, 'status'>): boolean =>
  task.status === 'queued' || task.status === 'running' || task.status === 'waiting';

/** "2 of 3 steps" style progress, or `null` before a plan exists. */
export const stepProgress = (task: Pick<RuntimeTaskSummary, 'step_count' | 'completed_step_count'>): string | null =>
  task.step_count === 0 ? null : `${task.completed_step_count} of ${task.step_count} step${task.step_count === 1 ? '' : 's'} done`;
