import { z } from 'zod';

/**
 * Response schemas for what Janus does (JAN-335, following JAN-142): runtime trigger runs,
 * trigger definitions and Janus-drafted proposals, the tool registry with its per-tool
 * policies and remote MCP servers, Agent Tasks (JAN-204), delegated tasks (JAN-208), and
 * the character profile and adaptive character state (JAN-301). Objects are
 * `z.looseObject`; the wrappers annotate their answer with the hand-authored type.
 */

// --- Runtime trigger runs ---------------------------------------------------

const runStatusSchema = z.enum(['queued', 'running', 'waiting', 'completed', 'failed', 'exhausted', 'cancelled']);
const definitionOriginSchema = z.enum(['user', 'system', 'agent']);
const definitionKindSchema = z.enum(['manual', 'schedule', 'event']);

export const runtimeTriggerSchema = z.looseObject({
  id: z.number().int(),
  definition_id: z.number().int().nullable().optional(),
  trigger_id: z.string(),
  task_id: z.string(),
  kind: z.enum(['manual', 'scheduled', 'event']),
  source: z.string(),
  status: runStatusSchema,
  created_at: z.string(),
  claimed_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  last_error: z.string().nullable().optional(),
  attempt_count: z.number().int(),
  next_attempt_at: z.string().nullable().optional(),
  result: z.string().nullable().optional(),
  step_error: z.string().nullable().optional(),
  definition_name: z.string().nullable().optional(),
  definition_origin: definitionOriginSchema.nullable().optional(),
});

export const runtimeTriggerListSchema = z.array(runtimeTriggerSchema);

const pageOf = <T extends z.ZodType>(item: T) =>
  z.looseObject({
    items: z.array(item),
    total: z.number().int(),
    limit: z.number().int(),
    offset: z.number().int(),
    has_more: z.boolean(),
  });

export const runtimeTriggerPageSchema = pageOf(runtimeTriggerSchema);

export const runtimeTriggerWorkerStatusSchema = z.looseObject({
  enabled: z.boolean(),
  running: z.boolean(),
});

// --- Trigger definitions and proposals --------------------------------------

const triggerScheduleSchema = z.looseObject({
  type: z.enum(['daily', 'interval', 'once']),
  at: z.string().nullable().optional(),
  time: z.string().nullable().optional(),
  days: z.array(z.number().int()).optional(),
  minutes: z.number().int().nullable().optional(),
});

/** One condition of an event definition's payload filter (JAN-363). */
const triggerEventConditionSchema = z.looseObject({
  field: z.string(),
  op: z.enum(['in', 'not_in', 'contains']),
  values: z.array(z.string()),
});

export const triggerDefinitionSchema = z.looseObject({
  id: z.number().int(),
  builtin_key: z.string().nullable(),
  name: z.string(),
  objective: z.string(),
  tool_ids: z.array(z.string()),
  kind: definitionKindSchema,
  schedule: triggerScheduleSchema.nullable(),
  timezone: z.string(),
  event_type: z.string().nullable(),
  event_filter: z.array(triggerEventConditionSchema).nullable().optional(),
  max_runs_per_hour: z.number().int().nullable().optional(),
  rate_limited_count: z.number().int().nonnegative().optional(),
  last_rate_limited_at: z.string().nullable().optional(),
  enabled: z.boolean(),
  origin: definitionOriginSchema,
  llm_provider: z.string().nullable(),
  llm_model: z.string().nullable(),
  max_tool_calls: z.number().int().nullable().optional(),
  max_run_seconds: z.number().int().nullable().optional(),
  next_run_at: z.string().nullable(),
  last_run_at: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  proposal_status: z.enum(['proposed', 'approved', 'rejected', 'withdrawn', 'superseded']).nullable().optional(),
  proposal_rationale: z.string().nullable().optional(),
  proposed_at: z.string().nullable().optional(),
  proposal_source: z
    .looseObject({
      conversation_id: z.number().int().nullable().optional(),
      untrusted_context: z.array(z.string()).optional(),
      injection_suspected: z.boolean().optional(),
    })
    .nullable()
    .optional(),
  supersedes_id: z.number().int().nullable().optional(),
  approved_at: z.string().nullable().optional(),
  rejected_at: z.string().nullable().optional(),
  rejection_reason: z.string().nullable().optional(),
});

export const triggerDefinitionListSchema = z.array(triggerDefinitionSchema);

export const triggerProposalListSchema = z.array(
  triggerDefinitionSchema.extend({
    proposal_status: z.enum(['proposed', 'approved', 'rejected', 'withdrawn', 'superseded']),
    blocked_tool_ids: z.array(z.string()),
    untrusted_context: z.array(z.string()),
    injection_suspected: z.boolean(),
    next_occurrence: z.string().nullable(),
    supersedes: z
      .looseObject({
        id: z.number().int(),
        name: z.string(),
        objective: z.string(),
        tool_ids: z.array(z.string()),
        kind: definitionKindSchema,
        schedule: triggerScheduleSchema.nullable(),
        timezone: z.string(),
        event_type: z.string().nullable(),
        enabled: z.boolean(),
      })
      .nullable(),
  })
);

export const triggerProposalCountSchema = z.looseObject({ open: z.number().int().nonnegative() });

export const triggerEventCatalogSchema = z.array(
  z.looseObject({
    subsystem: z.string(),
    label: z.string(),
    description: z.string(),
    events: z.array(
      z.looseObject({
        event_type: z.string(),
        label: z.string(),
        description: z.string(),
        payload: z.array(z.looseObject({ name: z.string(), description: z.string() })),
        default_max_runs_per_hour: z.number().int().nullable().optional(),
      })
    ),
  })
);

// --- Tool registry, policies and MCP servers ---------------------------------

const chatModeSchema = z.enum(['allowed', 'ask', 'always', 'off']);
const policyModeSchema = z.enum(['per_tool', 'allow_all']);

/** The user's blanket permission modes (JAN-359). */
export const toolPolicyModesSchema = z.looseObject({
  unattended_mode: policyModeSchema,
  chat_mode: policyModeSchema,
});

export const toolPolicySchema = z.looseObject({
  tool_id: z.string(),
  unattended_allowed: z.boolean(),
  default_unattended_allowed: z.boolean(),
  chat_mode: chatModeSchema,
  default_chat_mode: chatModeSchema,
  overridden: z.boolean(),
  locked_reason: z.string().nullable().optional(),
  effective_unattended_allowed: z.boolean(),
  effective_chat_asks: z.boolean(),
  withheld_unattended_tools: z.array(z.string()),
});

export const mcpToolSchema = z.looseObject({
  id: z.number().int(),
  upstream_name: z.string(),
  namespaced_id: z.string(),
  description: z.string(),
  input_schema: z.record(z.string(), z.unknown()),
  enabled: z.boolean(),
  read_only: z.boolean().optional(),
  confirmation: z.enum(['never', 'required']).optional(),
  injection_suspected: z.boolean().optional(),
  injection_acknowledged: z.boolean().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const mcpServerSchema = z.looseObject({
  id: z.number().int(),
  provider_name: z.string(),
  endpoint: z.string(),
  transport: z.string(),
  enabled: z.boolean(),
  discovery_status: z.string(),
  last_health_status: z.string().nullable().optional(),
  last_health_at: z.string().nullable().optional(),
  last_error: z.string().nullable().optional(),
  oauth_required: z.boolean(),
  oauth_configured: z.boolean(),
  credential_configured: z.boolean(),
  credential_status: z.enum(['none', 'oauth', 'static']),
  tools: z.array(mcpToolSchema),
  created_at: z.string(),
  updated_at: z.string(),
});

export const mcpServerListSchema = z.array(mcpServerSchema);

export const mcpDiscoverySchema = z.looseObject({
  server: mcpServerSchema,
  discovered_count: z.number().int(),
});

export const mcpOAuthStartSchema = z.looseObject({ authorization_url: z.string() });

export const mcpRegistrySchema = z.looseObject({
  builtins: z.array(
    z.looseObject({
      id: z.string(),
      display_name: z.string(),
      description: z.string(),
      required_capabilities: z.array(z.string()),
      operation: z.string(),
      risk_level: z.string(),
      confirmation: z.string(),
      docs_ref: z.string().nullable().optional(),
      source: z.literal('builtin'),
      unattended_allowed: z.boolean().optional(),
      default_unattended_allowed: z.boolean().optional(),
      chat_mode: chatModeSchema.optional(),
      default_chat_mode: chatModeSchema.optional(),
      locked_reason: z.string().nullable().optional(),
      effective_unattended_allowed: z.boolean().optional(),
      effective_chat_asks: z.boolean().optional(),
      withheld_unattended_tools: z.array(z.string()).optional(),
      connected: z.boolean().optional(),
    })
  ),
  remote_servers: z.array(mcpServerSchema),
});

// --- Remote A2A agents (JAN-404) ---------------------------------------------

export const a2aAgentSchema = z.looseObject({
  id: z.number().int(),
  name: z.string(),
  base_url: z.string(),
  enabled: z.boolean(),
  card_name: z.string().nullable().optional(),
  card_description: z.string().nullable().optional(),
  protocol_version: z.string().nullable().optional(),
  skills: z.array(z.looseObject({ name: z.string(), description: z.string() })),
  rpc_url: z.string().nullable().optional(),
  token_configured: z.boolean(),
  last_error: z.string().nullable().optional(),
  last_checked_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const a2aAgentListSchema = z.array(a2aAgentSchema);

// --- Inbound webhooks (JAN-406) ----------------------------------------------

export const webhookSchema = z.looseObject({
  id: z.number().int(),
  name: z.string(),
  key_prefix: z.string(),
  enabled: z.boolean(),
  received_count: z.number().int(),
  last_received_at: z.string().nullable().optional(),
  created_at: z.string(),
});

export const webhookListSchema = z.array(webhookSchema);

export const webhookSecretSchema = z.looseObject({
  webhook: webhookSchema,
  path: z.string().nullable().optional(),
  secret: z.string(),
});

// --- Agent Tasks (JAN-204) and delegated tasks (JAN-208) ---------------------

const runtimeTaskSummarySchema = z.looseObject({
  source: z.enum(['trigger', 'definition', 'delegated']),
  ref_id: z.string(),
  task_id: z.string(),
  title: z.string(),
  status: runStatusSchema,
  created_at: z.string(),
  completed_at: z.string().nullable(),
  plan_status: z.string().nullable(),
  step_count: z.number().int(),
  completed_step_count: z.number().int(),
  failed_step_count: z.number().int(),
  current_step: z.string().nullable(),
  definition_id: z.number().int().nullable(),
  definition_name: z.string().nullable(),
  conversation_id: z.number().int().nullable(),
  child_count: z.number().int(),
});

export const runtimeTaskPageSchema = pageOf(runtimeTaskSummarySchema);

const runtimeStepTraceSchema = z.looseObject({
  model: z.string().nullable(),
  authorized_tools: z.array(z.string()),
  invoked_tools: z.array(z.string()),
  skipped: z.string().nullable(),
  refused: z.string().nullable(),
  failed: z.string().nullable(),
  suspected_injection: z.string().nullable(),
  // Which toolboxes the brain picked for the step (JAN-300); absent on older runs.
  tool_selection: z.string().nullable().optional(),
});

export const runtimeTaskDetailSchema = runtimeTaskSummarySchema.extend({
  objective: z.string().nullable(),
  task_mode: z.string().nullable(),
  node: z.string().nullable(),
  reason: z.string().nullable(),
  steps: z.array(
    z.looseObject({
      step_id: z.string(),
      objective: z.string(),
      status: z.string(),
      depends_on: z.array(z.string()),
      required_tool_ids: z.array(z.string()),
      trace: runtimeStepTraceSchema.nullable(),
      output: z.string().nullable(),
      error: z.string().nullable(),
    })
  ),
  trigger_source: z.string().nullable(),
  attempt_count: z.number().int().nullable(),
  last_error: z.string().nullable(),
  allowed_tool_ids: z.array(z.string()),
  delegation_result: z.string().nullable(),
  delegation_error: z.string().nullable(),
  parent: z.looseObject({ kind: z.enum(['trigger', 'delegated']), ref_id: z.string() }).nullable(),
  children: z.array(runtimeTaskSummarySchema),
});

export const delegatedTaskPageSchema = pageOf(
  z.looseObject({
    delegation_id: z.string(),
    status: z.enum(['queued', 'running', 'completed', 'failed']),
    objective: z.string(),
    requested_tool_ids: z.array(z.string()),
    allowed_tool_ids: z.array(z.string()),
    result: z.string().nullable(),
    error: z.string().nullable(),
    depth: z.number().int(),
    created_at: z.string(),
    claimed_at: z.string().nullable(),
    completed_at: z.string().nullable(),
  })
);

// --- Character profile and adaptive state (JAN-301) --------------------------

export const characterProfileSchema = z.looseObject({
  constitution_id: z.string(),
  version: z.number().int(),
  name: z.string(),
  identity: z.string(),
  core_values: z.array(z.string()),
  non_negotiables: z.array(z.string()),
  traits: z.array(
    z.looseObject({ name: z.string(), baseline: z.number(), max_delta: z.number(), description: z.string() })
  ),
  exploration_rate: z.number(),
  additional_instructions: z.string().nullable().optional(),
  memory_instructions: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});

export const characterPromptPreviewSchema = z.looseObject({
  system_prompt: z.string(),
  memory_instructions: z.string(),
});

const toneSchema = z.enum(['neutral', 'warm', 'formal', 'direct', 'playful']);
const verbositySchema = z.enum(['brief', 'balanced', 'detailed']);
const proactivitySchema = z.enum(['reactive', 'balanced', 'proactive']);

const characterStateValuesSchema = z.looseObject({
  tone: toneSchema,
  verbosity: verbositySchema,
  proactivity: proactivitySchema,
  current_focus: z.string().nullable(),
});

const dimensionOf = <T extends z.ZodType>(value: T) => z.looseObject({ default: value, allowed: z.array(value) });

export const characterStateViewSchema = z.looseObject({
  state: characterStateValuesSchema,
  defaults: characterStateValuesSchema,
  envelope: z.looseObject({
    tone: dimensionOf(toneSchema),
    verbosity: dimensionOf(verbositySchema),
    proactivity: dimensionOf(proactivitySchema),
    focus_allowed: z.boolean(),
  }),
  revision: z.number().int(),
  updated_at: z.string().nullable().optional(),
  focus_max_chars: z.number().int(),
});

export const characterStateHistoryPageSchema = z.looseObject({
  items: z.array(
    z.looseObject({
      id: z.number().int(),
      revision: z.number().int(),
      actor: z.enum(['owner', 'janus']),
      action: z.enum(['edit', 'proposal', 'rollback', 'reset', 'envelope']),
      chat_turn_id: z.string().nullable().optional(),
      proposal_id: z.number().int().nullable().optional(),
      rollback_of_id: z.number().int().nullable().optional(),
      previous_state: characterStateValuesSchema,
      new_state: characterStateValuesSchema,
      created_at: z.string(),
    })
  ),
  next_before_id: z.number().int().nullable(),
});

export const characterStateProposalSchema = z.looseObject({
  id: z.number().int(),
  status: z.enum(['proposed', 'approved', 'rejected']),
  changes: z.looseObject({
    tone: toneSchema.optional(),
    verbosity: verbositySchema.optional(),
    proactivity: proactivitySchema.optional(),
    current_focus: z.string().nullable().optional(),
  }),
  rationale: z.string(),
  chat_turn_id: z.string().nullable().optional(),
  conversation_id: z.number().int().nullable().optional(),
  created_at: z.string(),
  decided_at: z.string().nullable().optional(),
});

export const characterStateProposalListSchema = z.array(characterStateProposalSchema);
