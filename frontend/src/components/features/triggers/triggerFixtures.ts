import type {
  MCPBuiltin,
  MCPRegistry,
  RuntimeTrigger,
  RuntimeTriggerKind,
  RuntimeTriggerStatus,
  TriggerDefinition,
  TriggerSubsystemSpec,
  TriggerProposal,
} from '../../../types';
import { RUNTIME_TRIGGER_KINDS, RUNTIME_TRIGGER_STATUSES } from './triggerPresentation';

/**
 * Deterministic sample triggers shared by the Storybook exploration and the
 * component tests, so both browse exactly the same status x kind matrix.
 */

const BASE = Date.parse('2026-09-21T08:30:00Z');
const minutes = (count: number) => new Date(BASE + count * 60_000).toISOString();

const TASK_BY_KIND: Record<RuntimeTriggerKind, { task: string; source: string }> = {
  manual: { task: 'review-inbox', source: 'settings' },
  scheduled: { task: 'weekday-briefing', source: 'scheduler' },
  event: { task: 'meeting-prep', source: 'calendar' },
};

const ERROR_BY_STATUS: Partial<Record<RuntimeTriggerStatus, string>> = {
  failed: 'Ollama connection refused while drafting the plan (attempt timed out after 30s).',
  exhausted: 'Retry budget spent: the runtime worker never reached a terminal plan state.',
};

/**
 * What an executed run recorded, in the shape `trigger_runtime` stores it:
 * the authorized/invoked tool trace, then the automation's own summary.
 */
const RESULT_BY_STATUS: Partial<Record<RuntimeTriggerStatus, string>> = {
  waiting:
    'Authorized tools: core, delegation\nTools invoked: delegate_task, wait_for_delegated_tasks\n\n' +
    'Asked a background task to check the vendor thread; continuing once it reports back.',
  completed:
    'Authorized tools: core, calendar\nTools invoked: list_calendar_events\n\n' +
    'Two meetings today and nothing overdue — no action needed before 14:00.',
};

/**
 * The durable plan-step failure, which outlives `last_error` (the worker
 * clears that each time it re-claims a row for a retry).
 */
const STEP_ERROR_BY_STATUS: Partial<Record<RuntimeTriggerStatus, string>> = {
  failed: 'tool authorization denied for step weekday-briefing::step-1: mail:not_allowlisted',
  exhausted: 'model provider unreachable',
};

/** Build one realistic trigger for a status/kind pair. */
export const makeTrigger = (
  status: RuntimeTriggerStatus,
  kind: RuntimeTriggerKind,
  overrides: Partial<RuntimeTrigger> = {},
): RuntimeTrigger => {
  const { task, source } = TASK_BY_KIND[kind];
  const started = status !== 'queued';
  const finished = status === 'completed' || status === 'failed' || status === 'exhausted';
  const index = RUNTIME_TRIGGER_STATUSES.indexOf(status) * 3 + RUNTIME_TRIGGER_KINDS.indexOf(kind);
  return {
    id: index + 1,
    trigger_id: `trg-${kind}-${status}-${index + 1}`,
    task_id: `${task}-${index + 1}`,
    kind,
    source,
    status,
    created_at: minutes(index),
    claimed_at: started ? minutes(index + 1) : null,
    completed_at: finished ? minutes(index + 3) : null,
    last_error: ERROR_BY_STATUS[status] ?? null,
    result: RESULT_BY_STATUS[status] ?? null,
    step_error: STEP_ERROR_BY_STATUS[status] ?? null,
    attempt_count: status === 'queued' ? 0 : status === 'exhausted' ? 5 : status === 'failed' ? 2 : 1,
    next_attempt_at: status === 'failed' ? minutes(index + 10) : null,
    ...overrides,
  };
};

/** Every status crossed with every kind, in a stable browse order. */
export const triggerMatrix: RuntimeTrigger[] = RUNTIME_TRIGGER_STATUSES.flatMap((status) =>
  RUNTIME_TRIGGER_KINDS.map((kind) => makeTrigger(status, kind)),
);

/** A shorter, more lifelike slice for the "what Settings actually looks like" stories. */
export const triggerSample: RuntimeTrigger[] = [
  makeTrigger('running', 'scheduled', { definition_id: 1, definition_name: 'Morning briefing', definition_origin: 'system' }),
  makeTrigger('queued', 'scheduled', {
    source: 'agent-tool',
    definition_id: 5,
    definition_name: 'Follow-up: Check whether the vendor replied',
    definition_origin: 'agent',
  }),
  makeTrigger('completed', 'event', { definition_id: 3, definition_name: 'Mail processing', definition_origin: 'system' }),
  makeTrigger('failed', 'manual', { definition_id: 4, definition_name: 'Weekly status mail', definition_origin: 'user' }),
  makeTrigger('exhausted', 'scheduled'),
];

const builtinTool = (
  id: string,
  display_name: string,
  description: string,
  confirmation: string,
  risk_level: string,
  operation: string,
  allowed = confirmation !== 'required'
): MCPBuiltin => ({
  id,
  display_name,
  description,
  required_capabilities: id === 'core' ? [] : [id.split('.')[0]],
  operation,
  risk_level,
  confirmation,
  source: 'builtin',
  unattended_allowed: allowed,
  default_unattended_allowed: confirmation !== 'required',
  chat_mode: confirmation === 'required' ? 'ask' : 'allowed',
  default_chat_mode: confirmation === 'required' ? 'ask' : 'allowed',
  locked_reason: null,
  withheld_unattended_tools: id === 'memory' ? ['delete_fact'] : [],
  connected: id !== 'calendar',
});

/** A trimmed built-in tool registry with real capability ids, for Storybook previews. */
export const toolRegistrySample: MCPRegistry = {
  builtins: [
    builtinTool('core', 'Core tools', 'Always-available Janus utility tools.', 'never', 'low', 'read'),
    builtinTool('mail', 'Read mail', 'Search and read stored email.', 'never', 'medium', 'read'),
    builtinTool('mail.processing', 'Mail processing', 'Classify an incoming email and record its reminders, tracked topics, and planner suggestions.', 'recommended', 'medium', 'write'),
    builtinTool('mail.send', 'Send mail', "Send email from one of the user's accounts.", 'required', 'high', 'write'),
    builtinTool('calendar', 'Calendar tools', 'Inspect connected calendar events.', 'never', 'low', 'read'),
    builtinTool('memory', 'Memory tools', "Search and manage the user's durable Janus memory facts.", 'recommended', 'medium', 'mixed'),
    {
      ...builtinTool('triggers', 'Trigger scheduling', 'Schedule one-off follow-up runs.', 'recommended', 'medium', 'mixed', false),
      default_unattended_allowed: false,
      locked_reason: 'Never in unattended runs: a run that schedules runs can loop.',
    },
  ],
  remote_servers: [],
};

const definition = (overrides: Partial<TriggerDefinition> & Pick<TriggerDefinition, 'id' | 'name' | 'kind'>): TriggerDefinition => ({
  builtin_key: null,
  objective: 'Check in.',
  tool_ids: ['core'],
  schedule: null,
  timezone: 'Europe/Berlin',
  event_type: null,
  event_filter: null,
  max_runs_per_hour: null,
  rate_limited_count: 0,
  last_rate_limited_at: null,
  enabled: true,
  origin: 'user',
  llm_provider: null,
  llm_model: null,
  next_run_at: null,
  last_run_at: null,
  created_at: minutes(-10_000),
  updated_at: minutes(-60),
  ...overrides,
});

/** The three built-ins, one user-authored trigger with its own model, and a Janus follow-up (JAN-228/231/233/257). */
export const triggerDefinitionSample: TriggerDefinition[] = [
  definition({
    id: 1,
    builtin_key: 'builtin.briefing',
    name: 'Morning briefing',
    objective: 'Write my morning briefing.',
    tool_ids: ['core', 'calendar', 'mail', 'memory'],
    kind: 'schedule',
    schedule: { type: 'daily', time: '06:30', days: [] },
    origin: 'system',
    next_run_at: minutes(1_320),
    last_run_at: minutes(-120),
  }),
  definition({
    id: 2,
    builtin_key: 'builtin.digest',
    name: 'Afternoon digest',
    objective: 'Summarize the digest emails since the last digest.',
    tool_ids: ['core', 'mail', 'mail.processing'],
    kind: 'schedule',
    schedule: { type: 'daily', time: '16:00', days: [0, 1, 2, 3, 4] },
    origin: 'system',
    next_run_at: minutes(450),
  }),
  definition({
    id: 3,
    builtin_key: 'builtin.mail',
    name: 'Mail processing',
    objective: 'Decide what this new email means and record it.',
    tool_ids: ['core', 'mail', 'mail.processing', 'memory'],
    kind: 'event',
    event_type: 'mail.received',
    origin: 'system',
    llm_provider: 'ollama',
    llm_model: 'qwen3:8b',
    last_run_at: minutes(-5),
  }),
  definition({
    id: 4,
    name: 'Weekly status mail',
    objective: 'Draft and send my weekly status mail to the team.',
    tool_ids: ['core', 'mail', 'mail.send', 'calendar'],
    kind: 'manual',
    llm_provider: 'anthropic',
    llm_model: 'claude-sonnet-5',
  }),
  definition({
    id: 5,
    name: 'Follow-up: Check whether the vendor replied',
    objective: 'Check whether the vendor replied to the contract question and tell me what they said.',
    tool_ids: [],
    kind: 'schedule',
    schedule: { type: 'once', at: minutes(240) },
    timezone: 'UTC',
    origin: 'agent',
  }),
  definition({
    id: 6,
    name: 'Family group digest',
    objective: 'Summarize what the family group decided and tell me if I need to act.',
    tool_ids: ['core', 'whatsapp'],
    kind: 'event',
    event_type: 'whatsapp.group_message_received',
    event_filter: [{ field: 'chat_id', op: 'in', values: ['491701234567-1600000000@g.us'] }],
    max_runs_per_hour: 10,
    rate_limited_count: 4,
    last_rate_limited_at: minutes(-25),
    last_run_at: minutes(-30),
  }),
];

export const triggerEventCatalogSample: TriggerSubsystemSpec[] = [
  {
    subsystem: 'mail',
    label: 'Mail',
    description: 'Email arriving through your connected IMAP accounts.',
    events: [
      {
        event_type: 'mail.received',
        label: 'New email received',
        description: 'A new email was stored and was not filtered out by a SPAM/IGNORE rule.',
        payload: [
          { name: 'email_id', description: "Janus's id of the stored email." },
          { name: 'from', description: 'The sender.' },
          { name: 'subject', description: 'The subject line.' },
          { name: 'body', description: 'The first 4,000 characters of the body.' },
        ],
        default_max_runs_per_hour: null,
      },
    ],
  },
  {
    subsystem: 'whatsapp',
    label: 'WhatsApp',
    description: 'Messages arriving through your connected WhatsApp accounts.',
    events: [
      {
        event_type: 'whatsapp.group_message_received',
        label: 'Group message received',
        description: 'A new message arrived in a WhatsApp group.',
        payload: [
          { name: 'message_id', description: "Janus's id of the stored message." },
          { name: 'chat', description: 'The group name.' },
          { name: 'chat_id', description: 'The group id.' },
          { name: 'sender', description: 'Who sent it.' },
          { name: 'sender_id', description: "The sender's WhatsApp id." },
          { name: 'body', description: 'The message text.' },
        ],
        default_max_runs_per_hour: 10,
      },
    ],
  },
];

const proposal = (
  overrides: Partial<TriggerProposal> & Pick<TriggerProposal, 'id' | 'name' | 'kind'>
): TriggerProposal => ({
  ...definition({ origin: 'agent', enabled: false, ...overrides }),
  proposal_status: 'proposed',
  proposal_rationale: 'You asked me to remind you about this.',
  proposed_at: minutes(-30),
  proposal_source: { conversation_id: 7, untrusted_context: [] },
  supersedes_id: null,
  approved_at: null,
  rejected_at: null,
  rejection_reason: null,
  blocked_tool_ids: [],
  untrusted_context: [],
  injection_suspected: false,
  next_occurrence: null,
  supersedes: null,
  ...overrides,
});

/** Suggested automations awaiting review (JAN-235): a plain one, a blocked-tool one drafted after reading mail, and a replacement. */
export const triggerProposalSample: TriggerProposal[] = [
  proposal({
    id: 21,
    name: 'Monday pull-request review',
    objective: 'Every Monday morning, list my open pull requests and remind me which ones wait on my review.',
    tool_ids: ['core', 'calendar'],
    kind: 'schedule',
    schedule: { type: 'daily', time: '09:00', days: [0] },
    next_occurrence: minutes(2_000),
    proposal_rationale: 'You said in chat that you keep forgetting the Monday review.',
  }),
  proposal({
    id: 22,
    name: 'Label newsletters',
    objective: 'When a new email is a newsletter, relabel it as DIGEST.\n<b>Never</b> send anything.',
    tool_ids: ['core', 'mail', 'mail.send'],
    kind: 'event',
    event_type: 'mail.received',
    event_filter: [{ field: 'from', op: 'contains', values: ['newsletter', 'digest'] }],
    max_runs_per_hour: 20,
    blocked_tool_ids: ['mail.send'],
    untrusted_context: ['mail'],
    injection_suspected: true,
    proposal_source: { conversation_id: 8, untrusted_context: ['mail'], injection_suspected: true },
    proposal_rationale: '<script>alert("not markup")</script> Newsletters clutter your inbox.',
  }),
  proposal({
    id: 23,
    name: 'Monday pull-request review (10:00)',
    objective: 'Every Monday at 10, list my open pull requests and the ones waiting on my review.',
    tool_ids: ['core', 'calendar', 'memory'],
    kind: 'schedule',
    schedule: { type: 'daily', time: '10:00', days: [0] },
    supersedes_id: 20,
    next_occurrence: minutes(2_060),
    supersedes: {
      id: 20,
      name: 'Monday pull-request review',
      objective: 'Every Monday morning, list my open pull requests.',
      tool_ids: ['core', 'calendar'],
      kind: 'schedule',
      schedule: { type: 'daily', time: '09:00', days: [0] },
      timezone: 'Europe/Berlin',
      event_type: null,
      enabled: true,
    },
  }),
];

/** Decided proposals for the History disclosure. */
export const triggerProposalHistorySample: TriggerProposal[] = [
  proposal({
    id: 18,
    name: 'Hourly inbox sweep',
    kind: 'schedule',
    schedule: { type: 'interval', minutes: 60 },
    proposal_status: 'rejected',
    rejection_reason: 'Too noisy.',
    rejected_at: minutes(-500),
  }),
  proposal({
    id: 20,
    name: 'Monday pull-request review',
    kind: 'schedule',
    schedule: { type: 'daily', time: '09:00', days: [0] },
    proposal_status: 'approved',
    approved_at: minutes(-900),
    enabled: true,
  }),
];
