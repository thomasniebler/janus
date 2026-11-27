import { z } from 'zod';
import { UI_VERSIONS } from '../contexts/uiVersion';
import { whatsappMessageListSchema } from './opsSchemas';

export const loginResponseSchema = z.looseObject({
  status: z.string(),
  token_type: z.string(),
  csrf_token: z.string(),
});

export const setupStatusSchema = z.looseObject({
  needs_setup: z.boolean(),
});

export const plannerSettingsSchema = z.looseObject({
  initial_sync_email_limit: z.number().int().positive(),
  llm_provider: z.enum(['ollama', 'openai', 'openrouter', 'gemini', 'anthropic', 'github_models', 'azure_openai']),
  llm_model: z.string(),
  ollama_model: z.string(),
  ollama_base_url: z.string(),
  ollama_api_key_configured: z.boolean(),
  openai_model: z.string(),
  openai_base_url: z.string().nullable().optional(),
  openai_api_key_configured: z.boolean(),
  openrouter_model: z.string(),
  openrouter_base_url: z.string().nullable().optional(),
  openrouter_api_key_configured: z.boolean(),
  gemini_model: z.string(),
  gemini_api_key_configured: z.boolean(),
  anthropic_model: z.string(),
  anthropic_base_url: z.string().nullable().optional(),
  anthropic_api_key_configured: z.boolean(),
  github_models_model: z.string(),
  github_models_base_url: z.string().nullable().optional(),
  github_models_api_key_configured: z.boolean(),
  azure_openai_endpoint: z.string().nullable().optional(),
  azure_openai_deployment: z.string().nullable().optional(),
  azure_openai_api_version: z.string(),
  azure_openai_auth_mode: z.enum(['entra', 'api_key']),
  azure_openai_api_key_configured: z.boolean(),
  github_token_configured: z.boolean(),
  github_default_repo: z.string().nullable().optional(),
  topic_tracking_available: z.boolean(),
  topic_tracking_default_enabled: z.boolean(),
  topic_tracking_default_scope: z.enum(['important_only', 'important_and_digest', 'all_non_spam']),
  topic_tracking_default_min_confidence: z.enum(['high', 'medium', 'low']),
  topic_tracking_default_min_confirmation_mentions: z.number().int().positive(),
  topic_tracking_max_active_topics: z.number().int().positive(),
  topic_tracking_max_context_topics: z.number().int().positive(),
  topic_tracking_review_required: z.boolean(),
  // Per-surface model overrides (JAN-233).
  chat_llm_provider: z.string().nullable().optional(),
  chat_llm_model: z.string().nullable().optional(),
  utility_llm_provider: z.string().nullable().optional(),
  utility_llm_model: z.string().nullable().optional(),
  // Semantic search (JAN-241).
  embedding_provider: z.enum(['ollama', 'openai', 'gemini']).nullable().optional(),
  embedding_model: z.string().nullable().optional(),
  embedding_min_similarity: z.number().min(0).max(1),
  embedding_default_models: z.record(z.enum(['ollama', 'openai', 'gemini']), z.string()),
  vector_search_available: z.boolean(),
  // Chat image generation (JAN-253).
  image_provider: z.enum(['openai', 'gemini', 'openrouter']).nullable().optional(),
  image_model: z.string().nullable().optional(),
  image_default_models: z.record(z.enum(['openai', 'gemini', 'openrouter']), z.string()).optional(),
  image_storage_limit_mb: z.number().int().positive().optional(),
});

const emailPrioritySchema = z.enum(['important', 'digest', 'ignore', 'spam']);

export const emailTopicBadgeSchema = z.looseObject({
  id: z.number(),
  title: z.string(),
  status: z.enum(['candidate', 'active', 'snoozed', 'resolved', 'ignored']),
  pinned: z.boolean(),
});

export const emailSchema = z.looseObject({
  id: z.number(),
  message_id: z.string(),
  sender: z.string(),
  subject: z.string(),
  snippet: z.string(),
  body: z.string().nullable().optional(),
  priority: emailPrioritySchema,
  reasoning: z.string().nullable().optional(),
  classification_confidence: z.enum(['high', 'medium', 'low']).nullable().optional(),
  received_at: z.string(),
  account_id: z.number(),
  topics: z.array(emailTopicBadgeSchema).default([]),
});

export const emailListSchema = z.array(emailSchema);

export const mailIngestionSettingsSchema = z.looseObject({
  initial_sync_email_limit: z.number().int().positive(),
});

export const secretarySettingsSchema = z.looseObject({
  secretary_active_start_hour: z.number().int().min(0).max(23),
  secretary_active_end_hour: z.number().int().min(1).max(24),
  secretary_whatsapp_enabled: z.boolean(),
  secretary_whatsapp_number: z.string().nullable(),
});

export const calendarEventSchema = z.looseObject({
  id: z.number(),
  kind: z.literal('event'),
  source: z.enum(['manual', 'email_agent', 'digest_agent', 'janus_agent', 'imported']),
  source_label: z.string(),
  read_only: z.boolean(),
  title: z.string(),
  description: z.string().nullable().optional(),
  start_time: z.string(),
  end_time: z.string(),
  location: z.string().nullable().optional(),
  all_day: z.boolean(),
  confidence: z.enum(['high', 'medium', 'low']).nullable().optional(),
  external_id: z.string().nullable().optional(),
  planner_account_id: z.number().nullable().optional(),
});

export const calendarEventListSchema = z.array(calendarEventSchema);

const taskBaseSchema = z.looseObject({
  id: z.number(),
  kind: z.literal('task'),
  source: z.enum(['manual', 'email_agent', 'digest_agent', 'janus_agent', 'imported']),
  source_label: z.string(),
  read_only: z.boolean(),
  title: z.string(),
  description: z.string().nullable().optional(),
  status: z.string(),
  parent_task_id: z.number().nullable().optional(),
  sort_order: z.number().nullable().optional(),
  child_count: z.number().optional(),
  open_child_count: z.number().optional(),
  due_at: z.string().nullable().optional(),
  scheduled_for: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  priority: z.string().nullable().optional(),
  confidence: z.enum(['high', 'medium', 'low']).nullable().optional(),
  external_id: z.string().nullable().optional(),
  planner_account_id: z.number().nullable().optional(),
});

type TaskSchemaOutput = z.infer<typeof taskBaseSchema> & { children?: TaskSchemaOutput[] };

/** A planner task; tree responses nest subtasks under `children` (JAN-317). */
export const taskSchema: z.ZodType<TaskSchemaOutput> = taskBaseSchema.extend({
  children: z.lazy(() => z.array(taskSchema)).optional(),
});

export const taskListSchema = z.array(taskSchema);

export const plannerSuggestionSchema = z.looseObject({
  id: z.number(),
  suggestion_type: z.enum(['task', 'event', 'task_update', 'event_update']),
  status: z.enum(['pending', 'approved', 'dismissed', 'applied']),
  source: z.enum(['manual', 'email_agent', 'digest_agent', 'janus_agent', 'imported']),
  confidence: z.enum(['high', 'medium', 'low']).nullable().optional(),
  reason: z.string().nullable().optional(),
  dedupe_key: z.string().nullable().optional(),
  title: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  task_status: z.string().nullable().optional(),
  priority: z.string().nullable().optional(),
  due_at: z.string().nullable().optional(),
  scheduled_for: z.string().nullable().optional(),
  start_time: z.string().nullable().optional(),
  end_time: z.string().nullable().optional(),
  location: z.string().nullable().optional(),
  all_day: z.boolean(),
  source_email_id: z.number().nullable().optional(),
  source_email_subject: z.string().nullable().optional(),
  source_email_sender: z.string().nullable().optional(),
  source_topic_id: z.number().nullable().optional(),
  source_topic_title: z.string().nullable().optional(),
  target_task_id: z.number().nullable().optional(),
  target_event_id: z.number().nullable().optional(),
  applied_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const plannerSuggestionListSchema = z.array(plannerSuggestionSchema);


export const topicSummarySchema = z.looseObject({
  id: z.number(),
  title: z.string(),
  summary: z.string().nullable().optional(),
  // Listed since JAN-142, when the schema was a stripping `z.object`.
  marker_keywords: z.array(z.string()).default([]),
  status: z.enum(['candidate', 'active', 'snoozed', 'resolved', 'ignored']),
  pinned: z.boolean(),
  user_confirmed: z.boolean(),
  origin: z.enum(['auto', 'manual', 'rule_suggestion']),
  confidence: z.enum(['high', 'medium', 'low']).nullable().optional(),
  first_seen_at: z.string(),
  last_seen_at: z.string(),
  snoozed_until: z.string().nullable().optional(),
  last_briefed_at: z.string().nullable().optional(),
  last_digested_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  evidence_count: z.number().int().nonnegative(),
});

export const topicSummaryListSchema = z.array(topicSummarySchema);

export const dashboardDataSchema = z.looseObject({
  important_emails: emailListSchema,
  whatsapp_messages: whatsappMessageListSchema,
  all_emails: emailListSchema,
  events: z.array(calendarEventSchema),
  tasks: z.array(taskSchema),
  topics: z.array(topicSummarySchema),
  now: z.string(),
});

export const dashboardWidgetKeySchema = z.enum(['briefing', 'topic_focus', 'important_emails', 'whatsapp_messages', 'schedule', 'tasks']);
export const briefingEmailWindowSchema = z.enum(['today', 'yesterday', 'last_24_hours']);

export const dashboardWidgetPreferenceSchema = z.looseObject({
  widget_key: dashboardWidgetKeySchema,
  is_visible: z.boolean(),
  is_collapsed: z.boolean(),
  prompt_override: z.string().nullable().optional(),
  briefing_email_window: briefingEmailWindowSchema.nullable().optional(),
});

export const dashboardWidgetPreferenceListSchema = z.array(dashboardWidgetPreferenceSchema);

const statusValueSchema = z.enum(['operational', 'degraded', 'outage', 'unknown']);

export const statusComponentSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  status: statusValueSchema,
  message: z.string(),
  updated_at: z.string().nullable().optional(),
  subtitle: z.string().nullable().optional(),
});

export const statusServiceSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  status: statusValueSchema,
  summary: z.string(),
  updated_at: z.string().nullable().optional(),
  components: z.array(statusComponentSchema),
});

export const statusIncidentSchema = z.looseObject({
  id: z.number(),
  timestamp: z.string(),
  status: z.enum(['degraded', 'outage', 'resolved']),
  title: z.string(),
  message: z.string(),
  service_id: z.string(),
  service_name: z.string(),
  component_name: z.string().nullable().optional(),
});

export const statusOverviewSchema = z.looseObject({
  overall_status: statusValueSchema,
  overall_message: z.string(),
  generated_at: z.string(),
  services: z.array(statusServiceSchema),
  recent_incidents: z.array(statusIncidentSchema),
});

export const versionInfoSchema = z.looseObject({
  version: z.string(),
  changelog_url: z.string(),
  release_url: z.string(),
  releases: z.array(
    z.looseObject({
      version: z.string(),
      date: z.string().nullable().optional(),
      groups: z.array(z.looseObject({ title: z.string(), items: z.array(z.string()) })),
    }),
  ),
});

export const healthSchema = z.looseObject({
  status: z.string(),
  version: z.string(),
  unhealthy_accounts: z.array(z.string()).optional(),
});

// ---------------------------------------------------------------------------
// JAN-142: auth/session, email account/rule/clarification, and planner/task
// mutations. Every schema in this file is a `z.looseObject` (JAN-335 moved the
// older ones too), so a field the backend adds (or one the schema does not list)
// passes through instead of being stripped; validation is about the fields the UI
// reads. `api.ts` annotates each wrapper with the
// hand-authored type from `types.ts`, so `tsc` proves the schema output fits it.
// ---------------------------------------------------------------------------

/** `{status}` answers, e.g. a password change. */
export const statusResultSchema = z.looseObject({
  status: z.string(),
});

/** `{status, message}` answers of the connection tests (IMAP account, LLM backend). */
export const connectionTestResultSchema = z.looseObject({
  status: z.string(),
  message: z.string(),
});

export const userSchema = z.looseObject({
  id: z.number().int(),
  username: z.string(),
  is_admin: z.boolean(),
  last_login: z.string().nullable().optional(),
  google_linked: z.boolean(),
  // A shell this build does not know (a newer backend) falls back to the default shell
  // instead of failing sign-in; `App.tsx` treats a missing value as 'v1'.
  ui_version: z.enum(UI_VERSIONS).optional().catch(undefined),
});

export const emailAccountSchema = z.looseObject({
  id: z.number().int(),
  name: z.string(),
  email: z.string(),
  imap_server: z.string(),
  imap_port: z.number().int(),
  username: z.string(),
  is_active: z.boolean(),
  /** `password` for a custom IMAP login, `google` / `microsoft` for a connected account. */
  auth_type: z.string().optional(),
});

export const emailAccountListSchema = z.array(emailAccountSchema);

export const ruleSchema = z.looseObject({
  id: z.number().int(),
  name: z.string(),
  pattern: z.string(),
  field: z.string(),
  priority: emailPrioritySchema,
  is_active: z.boolean(),
  order: z.number().int(),
});

export const ruleListSchema = z.array(ruleSchema);

const plannerAccountSchema = z.looseObject({
  id: z.number().int(),
  name: z.string(),
  provider: z.string(),
  api_url: z.string().nullable().optional(),
  is_active: z.boolean(),
  last_sync_at: z.string().nullable().optional(),
  last_sync_error: z.string().nullable().optional(),
});

export const plannerAccountListSchema = z.array(plannerAccountSchema);

export const llmModelListResponseSchema = z.looseObject({
  models: z.array(z.looseObject({ id: z.string(), label: z.string() })),
  error: z.string().nullable().optional(),
});

export type DashboardDataResponse = z.infer<typeof dashboardDataSchema>;
export type DashboardWidgetPreferenceResponse = z.infer<typeof dashboardWidgetPreferenceSchema>;
export type EmailResponse = z.infer<typeof emailSchema>;
export type MailIngestionSettingsResponse = z.infer<typeof mailIngestionSettingsSchema>;
export type SecretarySettingsResponse = z.infer<typeof secretarySettingsSchema>;
export type SetupStatusResponse = z.infer<typeof setupStatusSchema>;
export type PlannerSettingsResponse = z.infer<typeof plannerSettingsSchema>;
export type StatusOverviewResponse = z.infer<typeof statusOverviewSchema>;
export type HealthResponse = z.infer<typeof healthSchema>;
export type VersionInfo = z.infer<typeof versionInfoSchema>;
