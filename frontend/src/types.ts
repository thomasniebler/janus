import type { components } from './generated/api';
import type { UIVersion } from './contexts/uiVersion';
import type { ImageGenerationSettings, ImageGenerationSettingsUpdate } from './lib/imageGeneration';
import type {
  GitHubModelsAzureSettings,
  GitHubModelsAzureSettingsUpdate,
  LLMModelListRequest,
  LLMProviderId,
} from './lib/llmProviders';
import type { Task } from './lib/taskTree';

export type { LLMModelListRequest, LLMProviderId, Task, UIVersion };

type ApiSchemas = components['schemas'];

export type ConfidenceLevel = 'high' | 'medium' | 'low';
export type TopicStatus = 'candidate' | 'active' | 'snoozed' | 'resolved' | 'ignored';
export type PlannerItemSource = 'manual' | 'email_agent' | 'digest_agent' | 'janus_agent' | 'imported';
export type PlannerSuggestionStatus = 'pending' | 'approved' | 'dismissed' | 'applied';
export type PlannerSuggestionType = 'task' | 'event' | 'task_update' | 'event_update';

export interface EmailTopicBadge {
  id: number;
  title: string;
  status: TopicStatus;
  pinned: boolean;
}

export interface Email {
  id: number;
  message_id: string;
  sender: string;
  subject: string;
  snippet: string;
  body?: string | null;
  priority: 'important' | 'digest' | 'ignore' | 'spam';
  reasoning?: string | null;
  classification_confidence?: ConfidenceLevel | null;
  received_at: string;
  account_id: number;
  topics: EmailTopicBadge[];
}

export interface CalendarEvent {
  id: number;
  kind: 'event';
  source: PlannerItemSource;
  source_label: string;
  read_only: boolean;
  title: string;
  description?: string | null;
  start_time: string;
  end_time: string;
  location?: string | null;
  all_day: boolean;
  confidence?: ConfidenceLevel | null;
  external_id?: string | null;
  planner_account_id?: number | null;
}

export interface PlannerSuggestion {
  id: number;
  suggestion_type: PlannerSuggestionType;
  status: PlannerSuggestionStatus;
  source: PlannerItemSource;
  confidence?: ConfidenceLevel | null;
  reason?: string | null;
  dedupe_key?: string | null;
  title?: string | null;
  description?: string | null;
  task_status?: string | null;
  priority?: string | null;
  due_at?: string | null;
  scheduled_for?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  location?: string | null;
  all_day: boolean;
  source_email_id?: number | null;
  source_email_subject?: string | null;
  source_email_sender?: string | null;
  source_topic_id?: number | null;
  source_topic_title?: string | null;
  target_task_id?: number | null;
  target_event_id?: number | null;
  applied_at?: string | null;
  created_at: string;
  updated_at: string;
}

export type SystemEventType = 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR';

export interface SystemEvent {
  id: number;
  timestamp: string | null;
  source: string | null;
  message: string | null;
  event_type: SystemEventType | null;
  details?: string | null;
}

export interface SystemEventPage {
  items: SystemEvent[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface SystemEventQuery {
  event_type?: SystemEventType;
  source?: string;
  /** ISO-8601 inclusive lower bound. */
  start?: string;
  /** ISO-8601 exclusive upper bound. */
  end?: string;
  limit?: number;
  offset?: number;
}

export interface DashboardData {
  important_emails: Email[];
  whatsapp_messages: WhatsAppMessage[];
  all_emails: Email[];
  events: CalendarEvent[];
  tasks: Task[];
  topics: TopicSummary[];
  now: string;
}

export type BriefingEmailWindow = 'today' | 'yesterday' | 'last_24_hours';

export type DashboardWidgetKey = 'briefing' | 'topic_focus' | 'important_emails' | 'whatsapp_messages' | 'schedule' | 'tasks';

export interface DashboardWidgetPreference {
  widget_key: DashboardWidgetKey;
  is_visible: boolean;
  is_collapsed: boolean;
  prompt_override?: string | null;
  briefing_email_window?: BriefingEmailWindow | null;
}

export interface DashboardWidgetPreferenceUpdate {
  widget_key: DashboardWidgetKey;
  is_visible?: boolean;
  is_collapsed?: boolean;
  prompt_override?: string | null;
  briefing_email_window?: BriefingEmailWindow | null;
}

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  last_login?: string | null;
  google_linked: boolean;
  /** Which app shell this account uses. Older backends may omit it; treat that as 'v1'. */
  ui_version?: UIVersion;
}

export interface PasswordChangeRequest {
  current_password: string;
  new_password: string;
}

export interface EmailAccount {
  id: number;
  name: string;
  email: string;
  imap_server: string;
  imap_port: number;
  username: string;
  is_active: boolean;
  /** `password` for a custom IMAP login, `google` or `microsoft` for a connected account. */
  auth_type?: string;
}

export interface EmailAccountUpdate {
  name?: string;
  email?: string;
  imap_server?: string;
  imap_port?: number;
  username?: string;
  password?: string;
  is_active?: boolean;
}

export interface Rule {
  id: number;
  name: string;
  pattern: string;
  field: string;
  priority: 'important' | 'digest' | 'ignore' | 'spam';
  is_active: boolean;
  order: number;
}

export interface MailIngestionSettings {
  initial_sync_email_limit: number;
}

export interface MailIngestionSettingsUpdate {
  initial_sync_email_limit?: number;
}

export type WhatsAppConnectionStatus = 'disconnected' | 'connected' | 'degraded';
export type WhatsAppSetupStatus = 'created' | 'pairing' | 'awaiting_fallback' | 'ready' | 'completed' | 'failed' | 'cancelled' | 'expired';
export type WhatsAppPairingState =
  | 'idle'
  | 'starting'
  | 'waiting'
  | 'code_ready'
  | 'paired'
  | 'imported'
  | 'unsupported'
  | 'failed'
  | 'cancelled'
  | 'expired';
export type WhatsAppConversationType = 'direct' | 'group';
export type WhatsAppMessageDirection = 'incoming' | 'outgoing';

export interface WhatsAppAccount {
  id: number;
  display_name: string;
  provider: string;
  is_enabled: boolean;
  connection_status: WhatsAppConnectionStatus;
  initial_history_days: number;
  last_synced_at?: string | null;
  last_sync_error?: string | null;
  session_database_path?: string | null;
  session_configured: boolean;
}

export interface WhatsAppAccountCreate {
  display_name: string;
  is_enabled?: boolean;
  initial_history_days?: number;
  session_data?: string | null;
  session_database_path?: string | null;
}

export interface WhatsAppAccountUpdate {
  display_name?: string;
  is_enabled?: boolean;
  initial_history_days?: number;
  session_data?: string | null;
  session_database_path?: string | null;
}

export interface WhatsAppConversationSummary {
  id: number;
  whatsapp_account_id: number;
  provider_chat_id: string;
  title: string;
  conversation_type: WhatsAppConversationType;
  participant_summary?: string | null;
  unread_count: number;
  latest_message_at?: string | null;
  latest_message_snippet?: string | null;
}

export interface WhatsAppMessage {
  id: number;
  whatsapp_account_id: number;
  whatsapp_conversation_id: number;
  provider_message_id: string;
  provider_timestamp: string;
  direction: WhatsAppMessageDirection;
  sender_identifier?: string | null;
  sender_display_name?: string | null;
  body?: string | null;
  snippet: string;
  message_type?: string | null;
  attachment_summary?: string | null;
  reply_to_message_id?: string | null;
  priority: 'important' | 'digest' | 'ignore' | 'spam';
  reasoning?: string | null;
  classification_confidence?: ConfidenceLevel | null;
}

export interface WhatsAppSyncResult {
  account_id: number;
  connector_started: boolean;
  message: string;
}

export interface WhatsAppSetupSessionCreate {
  display_name: string;
  initial_history_days?: number;
  account_id?: number | null;
}

export interface WhatsAppSetupSessionFallbackImport {
  session_data?: string | null;
  session_database_path?: string | null;
}

export interface WhatsAppSetupSession {
  id: number;
  account_id?: number | null;
  display_name: string;
  initial_history_days: number;
  setup_status: WhatsAppSetupStatus;
  pairing_state: WhatsAppPairingState;
  pairing_supported: boolean;
  show_fallback_import: boolean;
  pairing_message?: string | null;
  pairing_code?: string | null;
  pairing_qr_payload?: string | null;
  last_error?: string | null;
  expires_at: string;
  completed_at?: string | null;
  session_configured: boolean;
}

export interface WhatsAppSetupCompleteResult {
  account: WhatsAppAccount;
  sync_result: WhatsAppSyncResult;
}

export interface PlannerAccount {
  id: number;
  name: string;
  provider: string;
  api_url?: string | null;
  is_active: boolean;
  last_sync_at?: string | null;
  last_sync_error?: string | null;
}

export interface GoogleCalendarOption {
  id: string;
  summary: string;
  primary: boolean;
  access_role: string;
  selected: boolean;
}

export interface LLMModelOption {
  id: string;
  label: string;
}

/** `error` is set (and `models` empty) when the provider lookup failed. */
export interface LLMModelListResponse {
  models: LLMModelOption[];
  error?: string | null;
}

export interface PlannerSettings extends ImageGenerationSettings, GitHubModelsAzureSettings {
  initial_sync_email_limit: number;
  llm_provider: LLMProviderId;
  llm_model: string;
  ollama_model: string;
  ollama_base_url: string;
  ollama_api_key_configured: boolean;
  openai_model: string;
  openai_base_url?: string | null;
  openai_api_key_configured: boolean;
  openrouter_model: string;
  openrouter_base_url?: string | null;
  openrouter_api_key_configured: boolean;
  gemini_model: string;
  gemini_api_key_configured: boolean;
  anthropic_model: string;
  anthropic_base_url?: string | null;
  anthropic_api_key_configured: boolean;
  /** Optional per-surface model overrides (JAN-233); null = default model. */
  chat_llm_provider?: string | null;
  chat_llm_model?: string | null;
  utility_llm_provider?: string | null;
  utility_llm_model?: string | null;
  github_token_configured: boolean;
  github_default_repo?: string | null;
  topic_tracking_available: boolean;
  topic_tracking_default_enabled: boolean;
  topic_tracking_default_scope: 'important_only' | 'important_and_digest' | 'all_non_spam';
  topic_tracking_default_min_confidence: ConfidenceLevel;
  topic_tracking_default_min_confirmation_mentions: number;
  topic_tracking_max_active_topics: number;
  topic_tracking_max_context_topics: number;
  topic_tracking_review_required: boolean;
  /** Semantic memory/topic search (JAN-241); null provider = embeddings off. */
  embedding_provider?: EmbeddingProviderId | null;
  embedding_model?: string | null;
  embedding_min_similarity: number;
  embedding_default_models: Record<EmbeddingProviderId, string>;
  vector_search_available: boolean;
}

/** Providers that can compute embeddings; they reuse the stored chat credentials. */
export type EmbeddingProviderId = 'ollama' | 'openai' | 'gemini';

export interface PlannerSettingsUpdate extends ImageGenerationSettingsUpdate, GitHubModelsAzureSettingsUpdate {
  initial_sync_email_limit?: number;
  llm_provider?: LLMProviderId;
  llm_model?: string;
  ollama_model?: string;
  ollama_base_url?: string;
  ollama_api_key?: string;
  openai_model?: string;
  openai_api_key?: string;
  openai_base_url?: string;
  openrouter_model?: string;
  openrouter_api_key?: string;
  openrouter_base_url?: string;
  gemini_model?: string;
  gemini_api_key?: string;
  anthropic_model?: string;
  anthropic_api_key?: string;
  anthropic_base_url?: string;
  github_token?: string;
  github_default_repo?: string;
  topic_tracking_available?: boolean;
  topic_tracking_default_enabled?: boolean;
  topic_tracking_default_scope?: 'important_only' | 'important_and_digest' | 'all_non_spam';
  topic_tracking_default_min_confidence?: ConfidenceLevel;
  topic_tracking_default_min_confirmation_mentions?: number;
  topic_tracking_max_active_topics?: number;
  topic_tracking_max_context_topics?: number;
  topic_tracking_review_required?: boolean;
  chat_llm_provider?: string | null;
  chat_llm_model?: string | null;
  utility_llm_provider?: string | null;
  utility_llm_model?: string | null;
  embedding_provider?: EmbeddingProviderId | null;
  embedding_model?: string | null;
  embedding_min_similarity?: number;
}

export interface TopicEvidence {
  id: number;
  source_type: 'email' | 'reminder' | 'calendar_event' | 'task' | 'memory_fact';
  source_id?: number | null;
  excerpt?: string | null;
  evidence_role: 'trigger' | 'supporting' | 'derived_link';
  created_at: string;
}

export interface TopicSummary {
  id: number;
  title: string;
  summary?: string | null;
  marker_keywords: string[];
  status: TopicStatus;
  pinned: boolean;
  user_confirmed: boolean;
  origin: 'auto' | 'manual' | 'rule_suggestion';
  confidence?: ConfidenceLevel | null;
  first_seen_at: string;
  last_seen_at: string;
  snoozed_until?: string | null;
  last_briefed_at?: string | null;
  last_digested_at?: string | null;
  created_at: string;
  updated_at: string;
  evidence_count: number;
}

export interface TopicDetail extends TopicSummary {
  evidence: TopicEvidence[];
}

export interface TopicUpdate {
  title?: string;
  summary?: string | null;
  marker_keywords?: string[];
  status?: TopicStatus;
  pinned?: boolean;
  snoozed_until?: string | null;
}

export interface TopicCreate {
  title: string;
  summary: string;
  marker_keywords: string[];
}

export interface DocNavItem {
  name: string;
  path?: string;
  children?: DocNavItem[];
}

export interface StatusComponent {
  id: string;
  name: string;
  status: 'operational' | 'degraded' | 'outage' | 'unknown';
  message: string;
  updated_at?: string | null;
  subtitle?: string | null;
}

export interface StatusService {
  id: string;
  name: string;
  status: 'operational' | 'degraded' | 'outage' | 'unknown';
  summary: string;
  updated_at?: string | null;
  components: StatusComponent[];
}

export interface StatusIncident {
  id: number;
  timestamp: string;
  status: 'degraded' | 'outage' | 'resolved';
  title: string;
  message: string;
  service_id: string;
  service_name: string;
  component_name?: string | null;
}

export interface StatusOverview {
  overall_status: 'operational' | 'degraded' | 'outage' | 'unknown';
  overall_message: string;
  generated_at: string;
  services: StatusService[];
  recent_incidents: StatusIncident[];
}

export interface ErrorGroup {
  id: number;
  fingerprint: string;
  source: string;
  message_template: string;
  first_seen: string;
  last_seen: string;
  occurrence_count: number;
  sample_details?: string | null;
  last_compacted_at?: string | null;
  created_at: string;
}

export interface ErrorCompactionResult {
  events_processed: number;
  groups_updated: number;
  events_deleted: number;
  groups_deleted: number;
}

export interface MCPTool extends Partial<Pick<ApiSchemas['MCPToolResponse'], 'read_only' | 'confirmation' | 'injection_suspected' | 'injection_acknowledged'>> {
  id: number;
  upstream_name: string;
  namespaced_id: string;
  description: string;
  input_schema: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface MCPServer extends Pick<ApiSchemas['MCPServerResponse'], 'credential_status'> {
  id: number;
  provider_name: string;
  endpoint: string;
  transport: string;
  enabled: boolean;
  discovery_status: 'never' | 'ready' | 'error' | 'auth_required' | string;
  last_health_status?: string | null;
  last_health_at?: string | null;
  last_error?: string | null;
  oauth_required: boolean;
  oauth_configured: boolean;
  credential_configured: boolean;
  tools: MCPTool[];
  created_at: string;
  updated_at: string;
}

export interface MCPBuiltin {
  id: string;
  display_name: string;
  description: string;
  required_capabilities: string[];
  operation: string;
  risk_level: string;
  confirmation: string;
  docs_ref?: string | null;
  source: 'builtin';
  /** Whether the user allows this capability in unattended runs (triggers, automations, background tasks). */
  unattended_allowed?: boolean;
  default_unattended_allowed?: boolean;
  /** The In chat switch (JAN-306, JAN-359): `allowed` (no switch), or for a confirmation-required tool `ask` / `always` / `off`. */
  chat_mode?: ToolChatMode;
  default_chat_mode?: ToolChatMode;
  /** Why the unattended switch is fixed (never available unattended), or null. */
  locked_reason?: string | null;
  /** Tool names of this toolbox that triggers Janus schedules itself never get. */
  withheld_unattended_tools?: string[];
  /** Whether the integrations this capability needs are connected. */
  connected?: boolean;
}

/** In chat: runs without asking (`allowed`/`always`), asks before every call (`ask`), or is withheld from chat (`off`). */
export type ToolChatMode = ApiSchemas['ToolPolicyResponse']['chat_mode'];
/** Fields of `PUT /api/tools/policies/{tool_id}`: an omitted field is left unchanged, `null` resets it. */
export type ToolPolicyChanges = ApiSchemas['ToolPolicyUpdate'];
export type ToolPolicy = ApiSchemas['ToolPolicyResponse'];

/** A registered remote A2A agent (JAN-404); its token is write-only and never returned. */
export type A2AAgent = ApiSchemas['A2AAgentResponse'];

/** An inbound webhook (JAN-406); its URL key and secret are never listed. */
export type Webhook = ApiSchemas['WebhookResponse'];
/** Returned once, at creation (with `path`) or after a secret rotation. */
export type WebhookSecret = ApiSchemas['WebhookSecretResponse'];

export interface MCPRegistry {
  builtins: MCPBuiltin[];
  remote_servers: MCPServer[];
}

export interface CharacterTrait {
  name: string;
  baseline: number;
  max_delta: number;
  description: string;
}

export interface CharacterProfile {
  constitution_id: string;
  version: number;
  name: string;
  identity: string;
  core_values: string[];
  non_negotiables: string[];
  traits: CharacterTrait[];
  /** Free text appended to the rendered constitution. */
  additional_instructions?: string | null;
  /** Replaces the built-in rules for which chat facts Janus remembers. */
  memory_instructions?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CharacterPromptPreview {
  system_prompt: string;
  memory_instructions: string;
}

export type CharacterProfileUpdate = Omit<
  CharacterProfile,
  'constitution_id' | 'version' | 'created_at' | 'updated_at'
>;

export type RuntimeTriggerKind = 'manual' | 'scheduled' | 'event';
export type RuntimeTriggerStatus = 'queued' | 'running' | 'waiting' | 'completed' | 'failed' | 'exhausted' | 'cancelled';

export interface RuntimeTrigger {
  id: number;
  /** The saved definition this run came from, if any. */
  definition_id?: number | null;
  trigger_id: string;
  task_id: string;
  kind: RuntimeTriggerKind;
  source: string;
  status: RuntimeTriggerStatus;
  created_at: string;
  claimed_at?: string | null;
  completed_at?: string | null;
  last_error?: string | null;
  attempt_count: number;
  next_attempt_at?: string | null;
  /**
   * What the run actually produced — the automation's summary plus the trace
   * of which tools it invoked — decoded from the trigger's durable plan state.
   * Null until the trigger has executed. Never contains the trigger payload.
   */
  result?: string | null;
  /**
   * The recorded plan-step failure. Outlives `last_error`, which the worker
   * clears whenever it re-claims a row for a retry, so an exhausted trigger
   * would otherwise show no reason at all.
   */
  step_error?: string | null;
  /** Name of the definition this run came from (JAN-256); null for ad-hoc runs. */
  definition_name?: string | null;
  definition_origin?: TriggerDefinitionOrigin | null;
}

/** One filtered, newest-first page of trigger runs (JAN-256). */
export interface RuntimeTriggerPage {
  items: RuntimeTrigger[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface RuntimeTriggerQuery {
  status?: RuntimeTriggerStatus;
  kind?: RuntimeTriggerKind;
  /** A definition id, or `'none'` for ad-hoc runs without a definition. */
  definition_id?: string;
  /** ISO-8601; inclusive lower created_at bound. */
  start?: string;
  /** ISO-8601; exclusive upper created_at bound. */
  end?: string;
  limit?: number;
  offset?: number;
}

/** Whether this Janus process executes queued runtime triggers. */
export type TriggerDefinitionKind = 'manual' | 'schedule' | 'event';
export type TriggerDefinitionOrigin = 'user' | 'system' | 'agent';

export interface TriggerSchedule {
  /** `once` is only ever set by Janus on its own chat follow-ups (JAN-257). */
  type: 'daily' | 'interval' | 'once';
  /** UTC ISO-8601 moment of a one-time follow-up; `once` only. */
  at?: string | null;
  /** HH:MM (24h) in the definition's timezone; daily only. */
  time?: string | null;
  /** 0 = Monday … 6 = Sunday; empty = every day. Daily only. */
  days?: number[];
  /** Interval only. */
  minutes?: number | null;
}

/**
 * A trigger definition. Event definitions (JAN-363) also carry `event_filter` (every
 * condition must match for an event to start a run; null = every event),
 * `max_runs_per_hour` (null = no limit), and `rate_limited_count` /
 * `last_rate_limited_at` (events the limit dropped). `max_tool_calls` / `max_run_seconds`
 * bound one run: null = Janus's default, 0 = no limit.
 */
export interface TriggerDefinition extends Pick<ApiSchemas['TriggerDefinitionCreate'], 'event_filter'>, Partial<Pick<ApiSchemas['TriggerDefinitionResponse'], 'max_runs_per_hour' | 'rate_limited_count' | 'last_rate_limited_at' | 'max_tool_calls' | 'max_run_seconds'>> {
  id: number;
  /** Set for Janus-seeded definitions (briefing, digest, mail processing). */
  builtin_key: string | null;
  name: string;
  objective: string;
  tool_ids: string[];
  kind: TriggerDefinitionKind;
  schedule: TriggerSchedule | null;
  timezone: string;
  event_type: string | null;
  enabled: boolean;
  origin: TriggerDefinitionOrigin;
  /** Optional model override (JAN-233); null = default model. */
  llm_provider: string | null;
  llm_model: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
  /** JAN-235: set only on definitions Janus drafted; the regular list only ever shows `approved` ones. */
  proposal_status?: TriggerProposalStatus | null;
  /** Janus's reason for the draft. Untrusted text: render it as plain text only. */
  proposal_rationale?: string | null;
  proposed_at?: string | null;
  proposal_source?: { conversation_id?: number | null; untrusted_context?: string[]; injection_suspected?: boolean } | null;
  supersedes_id?: number | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  rejection_reason?: string | null;
}

/** Review state of a trigger definition Janus drafted from chat (JAN-235). */
export type TriggerProposalStatus = 'proposed' | 'approved' | 'rejected' | 'withdrawn' | 'superseded';

/** The approved definition a proposal would replace, for the review diff. */
export interface TriggerProposalSupersedes {
  id: number;
  name: string;
  objective: string;
  tool_ids: string[];
  kind: TriggerDefinitionKind;
  schedule: TriggerSchedule | null;
  timezone: string;
  event_type: string | null;
  enabled: boolean;
}

/** A Janus-drafted definition with what its review card needs. */
export interface TriggerProposal extends TriggerDefinition {
  proposal_status: TriggerProposalStatus;
  /** Tools on the draft that are off for unattended runs; approving lets this definition use them. */
  blocked_tool_ids: string[];
  /** Untrusted-content tools the drafting chat turn had already read (e.g. `mail`, `web_search`). */
  untrusted_context: string[];
  /** Something read in the drafting turn looked like a prompt injection (JAN-127); labelled, not blocked. */
  injection_suspected: boolean;
  /** When it would first run if approved now (schedules only). */
  next_occurrence: string | null;
  supersedes: TriggerProposalSupersedes | null;
}

/** Approve a proposal; every field set here is the user's own edit. */
export interface TriggerProposalApproval extends Partial<Omit<TriggerDefinitionInput, 'enabled'>> {
  enable?: boolean;
}

/** `event_filter` / `max_runs_per_hour` (JAN-363) apply to event definitions only; an omitted limit on create takes the event's catalog default, null = no limit. */
export interface TriggerDefinitionInput extends Pick<ApiSchemas['TriggerDefinitionCreate'], 'event_filter' | 'max_runs_per_hour' | 'max_tool_calls' | 'max_run_seconds'> {
  name: string;
  objective: string;
  tool_ids: string[];
  kind: TriggerDefinitionKind;
  schedule?: TriggerSchedule | null;
  timezone: string;
  event_type?: string | null;
  enabled?: boolean;
  llm_provider?: string | null;
  llm_model?: string | null;
}

/** One field of an event's payload, as its producer sends it (JAN-210). */
export interface TriggerEventPayloadField {
  name: string;
  description: string;
}

/** A catalogued event a definition can subscribe to (JAN-210), with the run limit a new definition on it starts with (JAN-363). */
export interface TriggerEventSpec extends Pick<ApiSchemas['TriggerEventResponse'], 'default_max_runs_per_hour'> {
  /** The value stored on the definition, e.g. `mail.received`. */
  event_type: string;
  label: string;
  description: string;
  payload: TriggerEventPayloadField[];
}

/** A subsystem and the events it publishes, from `GET /api/runtime/definitions/event-catalog`. */
export interface TriggerSubsystemSpec {
  subsystem: string;
  label: string;
  description: string;
  events: TriggerEventSpec[];
}

export interface RuntimeTriggerWorkerStatus {
  /** Mirrors the `runtime_trigger_worker_enabled` setting (default: true). */
  enabled: boolean;
  /** True only while the worker thread is started and alive in this process. */
  running: boolean;
}

export interface RuntimeTriggerCreate {
  task_id: string;
  idempotency_key?: string;
  kind: RuntimeTriggerKind;
  source?: string;
  objective: string;
  task_mode?: string;
  max_steps?: number;
  schedule_id?: string;
  scheduled_for?: string;
  event_id?: string;
  occurred_at?: string;
}

export interface SkillSummary {
  id: number;
  name: string;
  description: string;
  license?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillDetail extends SkillSummary {
  content: string;
}

export interface RemoteSkillSummary {
  remote_id: string;
  name: string;
  description: string;
  license?: string | null;
  content?: string | null;
  source_url?: string | null;
}

export interface SkillImportRequest {
  remote_id: string;
  name?: string;
  description?: string;
  license?: string | null;
  content?: string;
}

export interface SkillDesignAssistRequest {
  goal: string;
  draft_name?: string;
  draft_description?: string;
  draft_content?: string;
}

export interface SkillDesignAssistResponse {
  suggested_name: string;
  suggested_description: string;
  suggested_content: string;
  guidance: string;
  follow_up_questions: string[];
}

export interface SkillCreate {
  name: string;
  description: string;
  license?: string | null;
  content: string;
}

export interface SkillUpdate {
  name?: string;
  description?: string;
  license?: string | null;
  content?: string;
}

export interface LinearSettings {
  api_key_configured: boolean;
  default_team_id: string | null;
}

export interface LinearSettingsUpdate {
  api_key?: string | null;
  default_team_id?: string | null;
}

/** Search backends that need no chat key of their own. */
export type IndependentSearchProvider = 'brave' | 'searxng';
/** Chat providers whose own search tool can answer; they reuse the key stored for chat. */
export type NativeSearchProvider = 'openai' | 'gemini' | 'anthropic' | 'openrouter';
export type WebSearchProvider = IndependentSearchProvider | NativeSearchProvider;

export interface WebSearchSettings {
  provider: WebSearchProvider | null;
  api_key_configured: boolean;
  base_url: string | null;
  /** Model for the native providers; null = that provider's default. */
  model?: string | null;
  default_models?: Partial<Record<NativeSearchProvider, string>>;
  /** Whether each native provider already has the chat key it reuses. */
  provider_keys?: Partial<Record<NativeSearchProvider, boolean>>;
  configured: boolean;
}

export interface WebSearchSettingsUpdate {
  provider?: WebSearchProvider | '' | null;
  api_key?: string | null;
  base_url?: string | null;
  model?: string | null;
}

export interface LinearIssueCreate {
  title: string;
  description: string;
  team_id?: string;
}

export interface LinearIssueResponse {
  identifier: string;
  title: string;
  url: string;
}

export interface GitHubSettings {
  token_configured: boolean;
  default_repo: string | null;
}

export interface GitHubSettingsUpdate {
  token?: string | null;
  default_repo?: string | null;
}

export interface GitHubIssueCreate {
  repo?: string;
  title: string;
  body: string;
  labels?: string[];
}

export interface GitHubIssueResponse {
  number: number;
  url: string;
  repo: string;
  title: string;
}

/** Lifecycle phase of one chat tool call (JAN-249); `start` means still running. */
export type ToolCallPhase = 'start' | 'end' | 'error' | 'blocked';

/**
 * The user's chat approval decision for a `confirmation="required"` call (JAN-254);
 * `null` on calls that needed none.
 */
export type ToolApprovalDecision = 'approved' | 'denied' | 'timeout' | 'disconnected' | 'unavailable';

/**
 * A `__TOOL_APPROVAL__` request: a paused call waiting for Approve / Deny (JAN-254).
 * `args` are redacted by the backend, like a tool call's.
 */
export interface ToolApprovalRequest {
  call_id: string;
  tool_id: string;
  tool_name: string;
  display_name: string;
  risk_level: string;
  reason: string;
  args: unknown;
  args_truncated: boolean;
  timeout_seconds: number;
}

/**
 * One chat tool call, from a live `__TOOL_EVENT__` frame or a stored
 * `tool_calls` row. `args` are redacted by the backend (a JSON value, or cut
 * JSON text when `args_truncated`); `result_preview` is untrusted plain text
 * and must never be rendered as Markdown or HTML.
 */
export interface ToolCallRecord {
  call_id: string;
  tool_id: string | null;
  tool_name: string;
  phase: ToolCallPhase;
  args: unknown;
  args_truncated: boolean;
  result_preview: string | null;
  result_truncated: boolean;
  duration_ms: number | null;
  /** The chat approval decision (JAN-254); absent or `null` when none was asked. */
  approval?: ToolApprovalDecision | null;
  /** Live only: the approval request while the call waits for the user (never stored). */
  approvalRequest?: ToolApprovalRequest;
  /** Live only: the answer this tab sent, so the card's buttons disable at once. */
  approvalAnswer?: 'approve' | 'deny' | 'always'; // `always`: also Always allow (JAN-359)
}

export interface ConversationMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  tool_calls?: ToolCallRecord[];
}

export interface ConversationSummary {
  id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
  /** `janus` when a trigger run started it with `start_conversation` (JAN-319); absent on older payloads. */
  origin?: 'user' | 'janus';
  /** A Janus-started conversation the user has not opened yet (JAN-319). */
  unread?: boolean;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface AgentMemoryFact {
  id: number;
  summary: string;
  detail: string | null;
  source: 'user' | 'agent' | 'compaction';
  status: 'active' | 'archived';
  source_conversation_id: number | null;
  compacted_into_id: number | null;
  created_at: string;
  updated_at: string;
  last_accessed_at: string | null;
  /** Times Janus's memory tools returned the fact. */
  access_count?: number;
  /** How far the nightly memory review checked the fact against its source conversation. */
  verification?: AgentMemoryVerification;
  verified_at?: string | null;
  /** The exact passage of the source conversation that supports the fact. */
  evidence_quote?: string | null;
  review_due_at?: string | null;
  /** Chosen by the memory review for the memory index injected into every prompt. */
  promoted?: boolean;
  /** The user's override of `promoted`: pinned into the index, hidden from it, or `null` (the review decides). */
  pin?: AgentMemoryPin | null;
  in_memory_index?: boolean;
}

export type AgentMemoryVerification = 'unverified' | 'confirmed' | 'corrected' | 'unsupported' | 'contradicted';
export type AgentMemoryPin = 'pinned' | 'hidden';

export interface AgentMemoryCompactionRun {
  id: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  result_summary: string | null;
  error_message: string | null;
  facts_considered: number;
  facts_archived: number;
  facts_created: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export type LoginRequest = ApiSchemas['LoginRequest'];
export type InitialSetupRequest = ApiSchemas['InitialSetupRequest'];
export type EmailAccountCreate = Omit<ApiSchemas['EmailAccountCreate'], 'is_active'> & {
  is_active?: ApiSchemas['EmailAccountCreate']['is_active'];
};
export type RuleCreate = Omit<ApiSchemas['RuleCreate'], 'is_active' | 'order'> & {
  is_active?: ApiSchemas['RuleCreate']['is_active'];
  order?: ApiSchemas['RuleCreate']['order'];
};
export type PlannerAccountCreate = Omit<ApiSchemas['PlannerAccountCreate'], 'is_active'> & {
  is_active?: ApiSchemas['PlannerAccountCreate']['is_active'];
};

/** One ingested knowledge source (JAN-242); its text lives in chunks the API does not return. */
export type KnowledgeSourceType =
  | 'email'
  | 'email_attachment'
  | 'conversation'
  | 'whatsapp_conversation'
  | 'calendar_event'
  | 'planner_task'
  | 'upload';

export type KnowledgeSourceStatus = 'pending' | 'ready' | 'skipped' | 'failed';

export interface KnowledgeSource {
  id: number;
  source_type: KnowledgeSourceType;
  owner_id?: number | null;
  part: number;
  title?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  status: KnowledgeSourceStatus;
  status_detail?: string | null;
  chunk_count: number;
  processed_at?: string | null;
  created_at: string;
}

/** Knowledge extraction settings and this month's token usage (JAN-243). */
export interface KnowledgeExtractionStatus {
  enabled: boolean;
  definition_id?: number | null;
  model: string;
  is_cloud: boolean;
  monthly_token_budget: number;
  period: string;
  tokens_used: number;
  calls: number;
  pending_chunks: number;
}

export type KnowledgeEntityType = 'person' | 'organization' | 'project' | 'place' | 'product' | 'concept';

export interface KnowledgeEntity {
  id: number;
  entity_type: KnowledgeEntityType;
  name: string;
  aliases: string[];
  description?: string | null;
  mention_count: number;
  relation_count: number;
}

/** A proposed merge of `duplicate` into `entity`, awaiting the user's decision. */
export interface KnowledgeMergeSuggestion {
  id: number;
  reason: string;
  created_at: string;
  entity: KnowledgeEntity;
  duplicate: KnowledgeEntity;
}

/** A reference to an entity inside a search hit or relation (JAN-243). */
export interface KnowledgeEntityRef {
  id: number;
  name: string;
  type: KnowledgeEntityType;
}

export interface KnowledgeSourceRef {
  id: number;
  type: KnowledgeSourceType;
  title?: string | null;
  owner_id?: number | null;
  date?: string | null;
}

/** One passage returned by knowledge search, with its source and why it matched. */
export interface KnowledgeSearchHit {
  chunk_id: number;
  text: string;
  source: KnowledgeSourceRef;
  entities: KnowledgeEntityRef[];
  matched_by: string[];
}

export interface KnowledgeSearchResult {
  hits: KnowledgeSearchHit[];
  entities: KnowledgeEntityRef[];
}

export interface KnowledgeRelationRef {
  id: number;
  direction: 'outgoing' | 'incoming';
  predicate: string;
  other: KnowledgeEntityRef;
  confidence: string;
  supporting_chunks: number;
}

export interface KnowledgeEntityDetail extends KnowledgeEntityRef {
  aliases: string[];
  description?: string | null;
  relations: KnowledgeRelationRef[];
  sources: (KnowledgeSourceRef & { mentions: number })[];
}

/** The source behind one `[n]` marker of a wiki page (JAN-244); a cited memory fact has `fact_id` and `memory_fact`. */
export interface WikiCitation {
  n: number;
  chunk_id?: number | null;
  fact_id?: number | null;
  source_id?: number | null;
  source_type?: KnowledgeSourceType | 'memory_fact' | null;
  title?: string | null;
  date?: string | null;
}

export interface WikiPageSummary {
  id: number;
  slug: string;
  title: string;
  subject_type: 'topic' | 'entity';
  subject_id?: number | null;
  user_edited: boolean;
  synthesized_at?: string | null;
  updated_at: string;
  pending_proposals: number;
}

export interface WikiRevision {
  id: number;
  body: string;
  citations: WikiCitation[];
  author: 'janus' | 'user' | 'agent';
  kind: 'published' | 'proposed';
  status?: 'pending' | 'accepted' | 'dismissed' | null;
  note?: string | null;
  created_at: string;
  resolved_at?: string | null;
}

export interface WikiPageDetail extends WikiPageSummary {
  body: string;
  citations: WikiCitation[];
  proposals: WikiRevision[];
  revisions: WikiRevision[];
}

export interface WikiSynthesisStatus {
  enabled: boolean;
  definition_id?: number | null;
  model: string;
  is_cloud: boolean;
  monthly_token_budget: number;
  period: string;
  tokens_used: number;
}

export type { StatsRangeDays, StatsSummary, TriggerDefinitionStats } from './statsTypes';
export type { ConnectionProvider, ConnectionStatus, ConnectionsOverview, SyncResult } from './connectionTypes';
