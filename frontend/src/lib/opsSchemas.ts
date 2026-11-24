import { z } from 'zod';

/**
 * Response schemas for connectors and operations (JAN-335, following JAN-142): WhatsApp
 * accounts, setup sessions, conversations and messages; Google calendar selection and the
 * OAuth URL; the system event log, error groups and activity stats. Objects are
 * `z.looseObject`; the wrappers annotate their answer with the hand-authored type.
 */

const prioritySchema = z.enum(['important', 'digest', 'ignore', 'spam']);
const confidenceSchema = z.enum(['high', 'medium', 'low']);

// --- WhatsApp ---------------------------------------------------------------

export const whatsappAccountSchema = z.looseObject({
  id: z.number().int(),
  display_name: z.string(),
  provider: z.string(),
  is_enabled: z.boolean(),
  connection_status: z.enum(['disconnected', 'connected', 'degraded']),
  initial_history_days: z.number().int(),
  last_synced_at: z.string().nullable().optional(),
  last_sync_error: z.string().nullable().optional(),
  session_database_path: z.string().nullable().optional(),
  session_configured: z.boolean(),
});

export const whatsappAccountListSchema = z.array(whatsappAccountSchema);

export const whatsappSyncResultSchema = z.looseObject({
  account_id: z.number().int(),
  connector_started: z.boolean(),
  message: z.string(),
});

export const whatsappSetupSessionSchema = z.looseObject({
  id: z.number().int(),
  account_id: z.number().int().nullable().optional(),
  display_name: z.string(),
  initial_history_days: z.number().int(),
  setup_status: z.enum(['created', 'pairing', 'awaiting_fallback', 'ready', 'completed', 'failed', 'cancelled', 'expired']),
  pairing_state: z.enum([
    'idle',
    'starting',
    'waiting',
    'code_ready',
    'paired',
    'imported',
    'unsupported',
    'failed',
    'cancelled',
    'expired',
  ]),
  pairing_supported: z.boolean(),
  show_fallback_import: z.boolean(),
  pairing_message: z.string().nullable().optional(),
  pairing_code: z.string().nullable().optional(),
  pairing_qr_payload: z.string().nullable().optional(),
  last_error: z.string().nullable().optional(),
  expires_at: z.string(),
  completed_at: z.string().nullable().optional(),
  session_configured: z.boolean(),
});

export const whatsappSetupCompleteSchema = z.looseObject({
  account: whatsappAccountSchema,
  sync_result: whatsappSyncResultSchema,
});

export const whatsappConversationListSchema = z.array(
  z.looseObject({
    id: z.number().int(),
    whatsapp_account_id: z.number().int(),
    provider_chat_id: z.string(),
    title: z.string(),
    conversation_type: z.enum(['direct', 'group']),
    participant_summary: z.string().nullable().optional(),
    unread_count: z.number().int(),
    latest_message_at: z.string().nullable().optional(),
    latest_message_snippet: z.string().nullable().optional(),
  })
);

export const whatsappMessageSchema = z.looseObject({
  id: z.number().int(),
  whatsapp_account_id: z.number().int(),
  whatsapp_conversation_id: z.number().int(),
  provider_message_id: z.string(),
  provider_timestamp: z.string(),
  direction: z.enum(['incoming', 'outgoing']),
  sender_identifier: z.string().nullable().optional(),
  sender_display_name: z.string().nullable().optional(),
  body: z.string().nullable().optional(),
  snippet: z.string(),
  message_type: z.string().nullable().optional(),
  attachment_summary: z.string().nullable().optional(),
  reply_to_message_id: z.string().nullable().optional(),
  priority: prioritySchema,
  reasoning: z.string().nullable().optional(),
  classification_confidence: confidenceSchema.nullable().optional(),
});

export const whatsappMessageListSchema = z.array(whatsappMessageSchema);

// --- Google calendars -------------------------------------------------------

export const calendarListSchema = z.array(
  z.looseObject({
    id: z.string(),
    summary: z.string(),
    primary: z.boolean(),
    access_role: z.string(),
    selected: z.boolean(),
  })
);

/** `GET /api/connections/<provider>/auth-url`: the browser is sent to it, so it must be an http(s) URL. */
export const authUrlSchema = z.looseObject({
  url: z.url({ protocol: /^https?$/ }),
});

// --- System events, error groups, stats --------------------------------------

export const systemEventPageSchema = z.looseObject({
  items: z.array(
    z.looseObject({
      id: z.number().int(),
      timestamp: z.string().nullable(),
      source: z.string().nullable(),
      message: z.string().nullable(),
      event_type: z.enum(['INFO', 'SUCCESS', 'WARNING', 'ERROR']).nullable(),
      details: z.string().nullable().optional(),
    })
  ),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
});

export const stringListSchema = z.array(z.string());

export const errorGroupSchema = z.looseObject({
  id: z.number().int(),
  fingerprint: z.string(),
  source: z.string(),
  message_template: z.string(),
  first_seen: z.string(),
  last_seen: z.string(),
  occurrence_count: z.number().int(),
  sample_details: z.string().nullable().optional(),
  last_compacted_at: z.string().nullable().optional(),
  created_at: z.string(),
});

export const errorGroupListSchema = z.array(errorGroupSchema);

export const errorCompactionResultSchema = z.looseObject({
  events_processed: z.number().int(),
  groups_updated: z.number().int(),
  events_deleted: z.number().int(),
  groups_deleted: z.number().int(),
});

const count = z.number().int().nonnegative();
const countsOf = <K extends string>(keys: readonly [K, ...K[]]) =>
  z.looseObject(Object.fromEntries(keys.map((key) => [key, count])) as Record<K, typeof count>);

const MAIL_KEYS = ['important', 'digest', 'ignore', 'spam'] as const;
const RUN_KEYS = ['completed', 'failed', 'cancelled', 'pending'] as const;
const MESSAGING_KEYS = ['whatsapp_in', 'whatsapp_out', 'chat_user', 'chat_assistant'] as const;
const AUTOMATION_DECISION_KEYS = ['proposed', 'approved', 'rejected', 'withdrawn', 'superseded'] as const;
const CHAT_TOOL_DECISION_KEYS = ['approved', 'denied', 'timeout', 'disconnected', 'unavailable'] as const;
const CHARACTER_DECISION_KEYS = ['proposed', 'approved', 'rejected'] as const;

const decisionsOf = <K extends string>(keys: readonly [K, ...K[]]) =>
  z.looseObject({
    totals: countsOf(keys),
    total: count,
    approval_rate: z.number().nullable(),
    median_hours_to_decision: z.number().nullable(),
  });

const perDayOf = <K extends string>(keys: readonly [K, ...K[]]) =>
  z.array(countsOf(keys).extend({ date: z.string() }));

export const statsSummarySchema = z.looseObject({
  days: z.literal([7, 30, 90]),
  timezone: z.string(),
  start: z.string(),
  end: z.string(),
  dates: z.array(z.string()),
  mail: z.looseObject({ per_day: perDayOf(MAIL_KEYS), totals: countsOf(MAIL_KEYS), total: count }),
  triggers: z.looseObject({
    per_day: perDayOf(RUN_KEYS),
    totals: countsOf(RUN_KEYS),
    total: count,
    success_rate: z.number().nullable(),
    definitions: z.array(
      z.looseObject({
        definition_id: z.number().int().nullable(),
        name: z.string().nullable(),
        origin: z.enum(['user', 'system', 'agent']).nullable(),
        runs: count,
        completed: count,
        failed: count,
        cancelled: count,
        pending: count,
        success_rate: z.number().nullable(),
        avg_duration_seconds: z.number().nullable(),
        last_run_at: z.string(),
      })
    ),
  }),
  memory: z.looseObject({
    per_day: z.array(z.looseObject({ date: z.string(), new_facts: count })),
    new_facts: count,
    active_facts: count,
    archived_facts: count,
    active_topics: count,
    total_topics: count,
    wiki_pages: count,
    knowledge_sources: count,
  }),
  messaging: z.looseObject({ per_day: perDayOf(MESSAGING_KEYS), totals: countsOf(MESSAGING_KEYS) }),
  approvals: z.looseObject({
    automations: decisionsOf(AUTOMATION_DECISION_KEYS),
    chat_tools: decisionsOf(CHAT_TOOL_DECISION_KEYS),
    character: decisionsOf(CHARACTER_DECISION_KEYS),
  }),
});

/** One provider's state in `GET /api/connections` (Settings -> Connections). */
export const connectionStatusSchema = z.looseObject({
  provider: z.enum(['google', 'microsoft']),
  configured: z.boolean(),
  connected: z.boolean(),
  account_email: z.string().nullable().optional(),
  mail_supported: z.boolean(),
  mail_ready: z.boolean(),
  redirect_uris: z.array(z.string()),
  config_keys: z.array(z.string()),
});

export const connectionsOverviewSchema = z.looseObject({
  google: connectionStatusSchema,
  microsoft: connectionStatusSchema,
});

/** `POST /api/planner/sync`: how many sources synced and the per-source errors. */
export const syncResultSchema = z.looseObject({
  synced_accounts: z.number().int(),
  errors: z.array(z.string()),
});
