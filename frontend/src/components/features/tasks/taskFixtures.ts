import type { RuntimeStepTrace, RuntimeTaskDetail, RuntimeTaskPage, RuntimeTaskSummary } from '../../../lib/runtimeTasks';

/** Sample Agent Tasks data (JAN-204) shared by stories and tests. */

const summaryBase: RuntimeTaskSummary = {
  source: 'definition',
  ref_id: 'run-weekly',
  task_id: 'definition:4:2026-10-05',
  title: 'Weekly review',
  status: 'completed',
  created_at: '2026-10-05T07:30:00Z',
  completed_at: '2026-10-05T07:31:12Z',
  plan_status: 'completed',
  step_count: 1,
  completed_step_count: 1,
  failed_step_count: 0,
  current_step: null,
  definition_id: 4,
  definition_name: 'Weekly review',
  conversation_id: null,
  child_count: 1,
};

export const taskSummarySample: RuntimeTaskSummary[] = [
  {
    ...summaryBase,
    source: 'trigger',
    ref_id: 'run-live',
    task_id: 'agent-follow-up',
    title: 'Check whether Dana replied about the offsite',
    status: 'running',
    created_at: '2026-10-05T09:00:00Z',
    completed_at: null,
    plan_status: 'active',
    completed_step_count: 0,
    current_step: 'Check whether Dana replied about the offsite',
    definition_id: null,
    definition_name: null,
    child_count: 0,
  },
  {
    ...summaryBase,
    source: 'delegated',
    ref_id: 'del-1',
    task_id: 'child-del-1',
    title: 'Summarize open invoices',
    created_at: '2026-10-05T07:30:40Z',
    completed_at: '2026-10-05T07:30:58Z',
    definition_id: null,
    definition_name: null,
    child_count: 0,
  },
  summaryBase,
  {
    ...summaryBase,
    ref_id: 'run-mail',
    task_id: 'definition:2:mail-17',
    title: 'Mail processing',
    status: 'exhausted',
    created_at: '2026-10-04T18:02:00Z',
    plan_status: 'failed',
    completed_step_count: 0,
    failed_step_count: 1,
    definition_id: 2,
    definition_name: 'Mail processing',
    child_count: 0,
  },
];

export const taskPage = (items: RuntimeTaskSummary[], total = items.length): RuntimeTaskPage => ({
  items,
  total,
  limit: 20,
  offset: 0,
  has_more: items.length < total,
});

export const completedTaskDetail: RuntimeTaskDetail = {
  ...summaryBase,
  objective: 'Review the week: open threads, upcoming deadlines, and anything I promised to follow up on.',
  task_mode: 'automation',
  node: 'complete',
  reason: null,
  trigger_source: 'definition.schedule',
  attempt_count: 1,
  last_error: null,
  allowed_tool_ids: [],
  delegation_result: null,
  delegation_error: null,
  parent: null,
  steps: [
    {
      step_id: 'step-1',
      objective: 'Review the week: open threads, upcoming deadlines, and anything I promised to follow up on.',
      status: 'completed',
      depends_on: [],
      required_tool_ids: ['mail.read', 'calendar.read'],
      trace: {
        model: 'ollama/llama3.1',
        authorized_tools: ['mail.read', 'calendar.read', 'mail.send'],
        invoked_tools: ['search_emails', 'list_events', 'send_email'],
        skipped: null,
        refused: 'send_email (off for unattended runs)',
        failed: null,
        suspected_injection: null,
      },
      output: 'Three threads need a reply; the budget review is due Thursday.\nI did not send anything.',
      error: null,
    },
  ],
  children: [taskSummarySample[1]],
};

export const failedTaskDetail: RuntimeTaskDetail = {
  ...completedTaskDetail,
  ...taskSummarySample[3],
  objective: 'Classify and file the new email.',
  node: 'failed',
  reason: 'step failed',
  attempt_count: 3,
  last_error: 'provider timed out',
  steps: [
    {
      step_id: 'step-1',
      objective: 'Classify and file the new email.',
      status: 'failed',
      depends_on: [],
      required_tool_ids: ['mail.processing'],
      trace: null,
      output: null,
      error: 'provider timed out',
    },
  ],
  children: [],
};

export const delegatedTaskDetail: RuntimeTaskDetail = {
  ...completedTaskDetail,
  ...taskSummarySample[1],
  objective: 'Summarize open invoices',
  task_mode: 'delegated',
  trigger_source: null,
  attempt_count: null,
  allowed_tool_ids: ['mail.read'],
  delegation_result: 'Two invoices are open: ACME (due Oct 10) and Initech (due Oct 31).',
  conversation_id: 12,
  parent: { kind: 'trigger', ref_id: 'run-weekly' },
  steps: [
    {
      ...completedTaskDetail.steps[0],
      objective: 'Summarize open invoices',
      required_tool_ids: ['mail.read'],
      trace: { ...(completedTaskDetail.steps[0].trace as RuntimeStepTrace), authorized_tools: ['mail.read'], invoked_tools: ['search_emails'], refused: null },
      output: 'Two invoices are open.',
    },
  ],
  children: [],
};

export const queuedTaskDetail: RuntimeTaskDetail = {
  ...completedTaskDetail,
  ...taskSummarySample[0],
  status: 'queued',
  objective: null,
  task_mode: null,
  node: null,
  plan_status: null,
  step_count: 0,
  current_step: null,
  steps: [],
  children: [],
};
