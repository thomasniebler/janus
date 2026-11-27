import type { AxiosResponse } from 'axios';
import type { components } from './generated/api';
import type {
  AgentMemoryCompactionRun,
  AgentMemoryFact,
  KnowledgeEntity,
  KnowledgeEntityDetail,
  KnowledgeEntityType,
  KnowledgeExtractionStatus,
  KnowledgeMergeSuggestion,
  KnowledgeSearchResult,
  KnowledgeSource,
  KnowledgeSourceStatus,
  KnowledgeSourceType,
  WikiPageDetail,
  WikiPageSummary,
  WikiSynthesisStatus,
  CalendarEvent,
  CharacterProfile,
  CharacterProfileUpdate,
  CharacterPromptPreview,
  ConversationDetail,
  ConversationSummary,
  DashboardData,
  DashboardWidgetPreference,
  DashboardWidgetPreferenceUpdate,
  DocNavItem,
  Email,
  EmailAccount,
  EmailAccountCreate,
  ErrorCompactionResult,
  ErrorGroup,
  GitHubIssueCreate,
  GitHubIssueResponse,
  GitHubSettings,
  GitHubSettingsUpdate,
    LinearIssueCreate,
    LinearIssueResponse,
    LinearSettings,
    LinearSettingsUpdate,
    WebSearchSettings,
    WebSearchSettingsUpdate,
    EmailAccountUpdate,
  InitialSetupRequest,
    LoginRequest,
    MailIngestionSettings,
    MailIngestionSettingsUpdate,
  MCPRegistry,
  MCPServer,
  MCPTool,
  PasswordChangeRequest,
  SyncResult,
  PlannerAccount,
  PlannerAccountCreate,
  PlannerSuggestion,
  PlannerSettings,
  PlannerSettingsUpdate,
  LLMModelListRequest,
  LLMModelListResponse,
  RuntimeTrigger,
  RuntimeTriggerCreate,
  RuntimeTriggerPage,
  RuntimeTriggerQuery,
  RuntimeTriggerWorkerStatus,
  StatsRangeDays,
  StatsSummary,
  TriggerDefinition,
  TriggerDefinitionInput,
  TriggerSubsystemSpec,
  TriggerProposal,
  TriggerProposalApproval,
  TriggerProposalStatus,
  ToolPolicy,
  ToolPolicyChanges,
  Rule,
  RuleCreate,
  RemoteSkillSummary,
  SkillCreate,
  SkillDesignAssistRequest,
  SkillDesignAssistResponse,
  SkillDetail,
  SkillImportRequest,
  SkillSummary,
  SkillUpdate,
  StatusOverview,
  SystemEventPage,
  SystemEventQuery,
  Task,
  TopicDetail,
  TopicCreate,
  TopicSummary,
  TopicUpdate,
  UIVersion,
  User,
  WhatsAppAccount,
  WhatsAppAccountCreate,
  WhatsAppAccountUpdate,
  WhatsAppSetupCompleteResult,
  WhatsAppSetupSession,
  WhatsAppSetupSessionCreate,
  WhatsAppSetupSessionFallbackImport,
  WhatsAppConversationSummary,
  WhatsAppMessage,
  WhatsAppSyncResult,
} from './types';
import * as schemas from './lib/schemas';
import * as knowledgeSchemas from './lib/knowledgeSchemas';
import * as opsSchemas from './lib/opsSchemas';
import * as runtimeSchemas from './lib/runtimeSchemas';
import api, { setCsrfToken } from './lib/httpClient';
import { parseResponse } from './lib/apiValidation';
import type { Parsed } from './lib/apiValidation';

export const authApi = {
  login: async (data: LoginRequest) => {
    const response = await parseResponse(schemas.loginResponseSchema)(api.post('/api/auth/login', data));
    setCsrfToken(response.data.csrf_token);
    return response;
  },
  logout: async () => {
    const response = await api.post('/api/auth/logout');
    setCsrfToken(null);
    return response;
  },
  changePassword: (data: PasswordChangeRequest): Parsed<{ status: string }> =>
    parseResponse(schemas.statusResultSchema)(api.post('/api/auth/change-password', data)),
  getMe: (): Parsed<User> => parseResponse(schemas.userSchema)(api.get('/api/auth/me')),
  updateUIPreference: (uiVersion: UIVersion): Parsed<User> =>
    parseResponse(schemas.userSchema)(api.patch('/api/auth/me/ui-preference', { ui_version: uiVersion })),
  getSetupStatus: () => parseResponse(schemas.setupStatusSchema)(api.get('/api/auth/setup')),
  // Unvalidated on purpose (JAN-335): setup is one-shot and already committed when it answers, so
  // rejecting a drifted answer would report a finished setup as failed; logout and unlink answers
  // are never read and their failures are ignored.
  setup: (data: InitialSetupRequest) => api.post<User>('/api/auth/setup', data),
  unlinkGoogle: () => api.delete<{ status: string }>('/api/auth/google/link'),
};

export const dashboardApi = {
  getData: () => parseResponse(schemas.dashboardDataSchema)(api.get('/api/dashboard/')) as Promise<{ data: DashboardData }>,
  getImportantEmails: () => parseResponse(schemas.emailListSchema)(api.get('/api/dashboard/important-emails')) as Promise<{ data: Email[] }>,
  getWhatsAppMessages: (): Parsed<WhatsAppMessage[]> =>
    parseResponse(opsSchemas.whatsappMessageListSchema)(api.get('/api/dashboard/whatsapp-messages')),
  getTopics: () => parseResponse(schemas.topicSummaryListSchema)(api.get('/api/dashboard/topics')) as Promise<{ data: TopicSummary[] }>,
  getSchedule: () =>
    parseResponse(schemas.calendarEventListSchema)(api.get('/api/dashboard/events')) as Promise<{ data: CalendarEvent[] }>,
  getTasks: () => parseResponse(schemas.taskListSchema)(api.get('/api/dashboard/tasks')) as Promise<{ data: Task[] }>,
  getWidgets: () =>
    parseResponse(schemas.dashboardWidgetPreferenceListSchema)(
      api.get('/api/dashboard/widgets')
    ) as Promise<{ data: DashboardWidgetPreference[] }>,
  updateWidgets: (widgets: DashboardWidgetPreferenceUpdate[]) =>
    parseResponse(schemas.dashboardWidgetPreferenceListSchema)(
      api.patch('/api/dashboard/widgets', { widgets })
    ) as Promise<{ data: DashboardWidgetPreference[] }>,
  getEvents: (params?: SystemEventQuery): Parsed<SystemEventPage> =>
    parseResponse(opsSchemas.systemEventPageSchema)(api.get('/api/events', { params })),
  getEventSources: (): Parsed<string[]> => parseResponse(opsSchemas.stringListSchema)(api.get('/api/events/sources')),
};

export const emailApi = {
  getAccounts: (): Parsed<EmailAccount[]> => parseResponse(schemas.emailAccountListSchema)(api.get('/api/emails/accounts')),
  addAccount: (data: EmailAccountCreate): Parsed<EmailAccount> =>
    parseResponse(schemas.emailAccountSchema)(api.post('/api/emails/accounts', data)),
  updateAccount: (id: number, data: EmailAccountUpdate): Parsed<EmailAccount> =>
    parseResponse(schemas.emailAccountSchema)(api.patch(`/api/emails/accounts/${id}`, data)),
  deleteAccount: (id: number) => api.delete(`/api/emails/accounts/${id}`),
  testAccount: (id: number): Parsed<{ status: string; message: string }> =>
    parseResponse(schemas.connectionTestResultSchema)(api.post(`/api/emails/accounts/${id}/test`)),
  getRules: (): Parsed<Rule[]> => parseResponse(schemas.ruleListSchema)(api.get('/api/emails/rules')),
  addRule: (data: RuleCreate): Parsed<Rule> => parseResponse(schemas.ruleSchema)(api.post('/api/emails/rules', data)),
  updateRule: (id: number, data: { order?: number; is_active?: boolean }): Parsed<Rule> =>
    parseResponse(schemas.ruleSchema)(api.patch(`/api/emails/rules/${id}`, data)),
  deleteRule: (id: number) => api.delete(`/api/emails/rules/${id}`),
  /** The inbox (everything but spam), or only the emails of one priority, e.g. the spam folder (JAN-219). */
  listEmails: (limit: number = 100, priority?: Email['priority']) =>
    parseResponse(schemas.emailListSchema)(api.get('/api/emails/', { params: { limit, priority } })) as Promise<{ data: Email[] }>,
  updatePriority: (id: number, priority: string): Parsed<Email> =>
    parseResponse(schemas.emailSchema)(api.patch(`/api/emails/${id}/priority?priority=${priority}`)),
  getSecretarySettings: () => parseResponse(schemas.secretarySettingsSchema)(api.get('/api/emails/notifications/preferences')),
  updateSecretarySettings: (data: Partial<schemas.SecretarySettingsResponse>): Parsed<schemas.SecretarySettingsResponse> =>
    parseResponse(schemas.secretarySettingsSchema)(api.patch('/api/emails/notifications/preferences', data)),
};

export const mailIngestionApi = {
  getSettings: () =>
    parseResponse(schemas.mailIngestionSettingsSchema)(api.get('/api/emails/settings')) as Promise<{ data: MailIngestionSettings }>,
  updateSettings: (data: MailIngestionSettingsUpdate) =>
    parseResponse(schemas.mailIngestionSettingsSchema)(
      api.patch('/api/emails/settings', data)
    ) as Promise<{ data: MailIngestionSettings }>,
};

const wa = opsSchemas;
export const whatsappApi = {
  getAccounts: (): Parsed<WhatsAppAccount[]> => parseResponse(wa.whatsappAccountListSchema)(api.get('/api/whatsapp/accounts')),
  addAccount: (data: WhatsAppAccountCreate): Parsed<WhatsAppAccount> =>
    parseResponse(wa.whatsappAccountSchema)(api.post('/api/whatsapp/accounts', data)),
  updateAccount: (id: number, data: WhatsAppAccountUpdate): Parsed<WhatsAppAccount> =>
    parseResponse(wa.whatsappAccountSchema)(api.patch(`/api/whatsapp/accounts/${id}`, data)),
  deleteAccount: (id: number) => api.delete(`/api/whatsapp/accounts/${id}`),
  createSetupSession: (data: WhatsAppSetupSessionCreate): Parsed<WhatsAppSetupSession> =>
    parseResponse(wa.whatsappSetupSessionSchema)(api.post('/api/whatsapp/setup-sessions', data)),
  getSetupSession: (id: number): Parsed<WhatsAppSetupSession> =>
    parseResponse(wa.whatsappSetupSessionSchema)(api.get(`/api/whatsapp/setup-sessions/${id}`)),
  startPairing: (id: number): Parsed<WhatsAppSetupSession> =>
    parseResponse(wa.whatsappSetupSessionSchema)(api.post(`/api/whatsapp/setup-sessions/${id}/start-pairing`)),
  cancelSetupSession: (id: number): Parsed<WhatsAppSetupSession> =>
    parseResponse(wa.whatsappSetupSessionSchema)(api.post(`/api/whatsapp/setup-sessions/${id}/cancel`)),
  fallbackImport: (id: number, data: WhatsAppSetupSessionFallbackImport): Parsed<WhatsAppSetupSession> =>
    parseResponse(wa.whatsappSetupSessionSchema)(api.post(`/api/whatsapp/setup-sessions/${id}/fallback-import`, data)),
  completeSetupSession: (id: number): Parsed<WhatsAppSetupCompleteResult> =>
    parseResponse(wa.whatsappSetupCompleteSchema)(api.post(`/api/whatsapp/setup-sessions/${id}/complete`)),
  testAccount: (id: number): Parsed<{ status: string; message: string }> =>
    parseResponse(schemas.connectionTestResultSchema)(api.post(`/api/whatsapp/accounts/${id}/test`)),
  restartConnector: (id: number): Parsed<WhatsAppSyncResult> =>
    parseResponse(wa.whatsappSyncResultSchema)(api.post(`/api/whatsapp/accounts/${id}/sync`)),
  getConversations: (limit: number = 50): Parsed<WhatsAppConversationSummary[]> =>
    parseResponse(wa.whatsappConversationListSchema)(api.get('/api/whatsapp/conversations', { params: { limit } })),
  getMessages: (params?: { conversation_id?: number; days?: number; limit?: number }): Parsed<WhatsAppMessage[]> =>
    parseResponse(wa.whatsappMessageListSchema)(api.get('/api/whatsapp/messages', { params })),
  updatePriority: (id: number, priority: WhatsAppMessage['priority']): Parsed<WhatsAppMessage> =>
    parseResponse(wa.whatsappMessageSchema)(api.patch(`/api/whatsapp/messages/${id}/priority`, { priority })),
};

export const errorApi = {
  listGroups: (params?: { limit?: number; source?: string }): Parsed<ErrorGroup[]> =>
    parseResponse(opsSchemas.errorGroupListSchema)(
      api.get('/api/errors/groups', { params: { limit: params?.limit ?? 100, source: params?.source ?? undefined } })
    ),
  getGroup: (id: number): Parsed<ErrorGroup> => parseResponse(opsSchemas.errorGroupSchema)(api.get(`/api/errors/groups/${id}`)),
  deleteGroup: (id: number) => api.delete(`/api/errors/groups/${id}`),
  compact: (retentionDays: number = 28): Parsed<ErrorCompactionResult> =>
    parseResponse(opsSchemas.errorCompactionResultSchema)(
      api.post('/api/errors/compact', null, { params: { retention_days: retentionDays } })
    ),
};

export const plannerApi = {
  getAccounts: (): Parsed<PlannerAccount[]> => parseResponse(schemas.plannerAccountListSchema)(api.get('/api/planner/accounts')),
  addAccount: (data: PlannerAccountCreate) => api.post<PlannerAccount>('/api/planner/accounts', data),
  getEvents: () => parseResponse(schemas.calendarEventListSchema)(api.get('/api/planner/events')) as Promise<{ data: CalendarEvent[] }>,
  getTasks: () => parseResponse(schemas.taskListSchema)(api.get('/api/planner/tasks')) as Promise<{ data: Task[] }>,
  getLocalEvents: (params?: { start?: string; end?: string }) =>
    parseResponse(schemas.calendarEventListSchema)(api.get('/api/planner/local/events', { params })) as Promise<{ data: CalendarEvent[] }>,
  createLocalEvent: async (data: {
    title: string;
    description?: string | null;
    start_time: string;
    end_time: string;
    location?: string | null;
    all_day?: boolean;
  }): Promise<{ data: CalendarEvent }> => {
    const response = await parseResponse(schemas.calendarEventSchema)(api.post('/api/planner/local/events', data));
    return { data: response.data };
  },
  updateLocalEvent: async (id: number, data: Record<string, unknown>): Promise<{ data: CalendarEvent }> => {
    const response = await parseResponse(schemas.calendarEventSchema)(api.patch(`/api/planner/local/events/${id}`, data));
    return { data: response.data };
  },
  getLocalTasks: (includeCompleted: boolean = false) =>
    parseResponse(schemas.taskListSchema)(api.get('/api/planner/local/tasks', { params: { include_completed: includeCompleted } })) as Promise<{ data: Task[] }>,
  createLocalTask: async (data: {
    title: string;
    description?: string | null;
    status?: string;
    priority?: string | null;
    due_at?: string | null;
    scheduled_for?: string | null;
  }): Promise<{ data: Task }> => {
    const response = await parseResponse(schemas.taskSchema)(api.post('/api/planner/local/tasks', data));
    return { data: response.data };
  },
  updateLocalTask: async (id: number, data: Record<string, unknown>): Promise<{ data: Task }> => {
    const response = await parseResponse(schemas.taskSchema)(api.patch(`/api/planner/local/tasks/${id}`, data));
    return { data: response.data };
  },
  deleteLocalItem: (kind: 'tasks' | 'events', id: number) => api.delete(`/api/planner/local/${kind}/${id}`),
  getSuggestions: () =>
    parseResponse(schemas.plannerSuggestionListSchema)(api.get('/api/planner/suggestions')) as Promise<{ data: PlannerSuggestion[] }>,
  updateSuggestion: (id: number, data: Record<string, unknown>) =>
    parseResponse(schemas.plannerSuggestionSchema)(api.patch(`/api/planner/suggestions/${id}`, data)) as Promise<{ data: PlannerSuggestion }>,
  approveSuggestion: (id: number) =>
    parseResponse(schemas.plannerSuggestionSchema)(api.post(`/api/planner/suggestions/${id}/approve`)) as Promise<{ data: PlannerSuggestion }>,
  dismissSuggestion: (id: number) =>
    parseResponse(schemas.plannerSuggestionSchema)(api.post(`/api/planner/suggestions/${id}/dismiss`)) as Promise<{ data: PlannerSuggestion }>,
  getSettings: () =>
    parseResponse(schemas.plannerSettingsSchema)(api.get('/api/planner/settings')) as Promise<{ data: PlannerSettings }>,
  updateSettings: (data: PlannerSettingsUpdate) =>
    parseResponse(schemas.plannerSettingsSchema)(api.patch('/api/planner/settings', data)) as Promise<{ data: PlannerSettings }>,
  testLLM: (data: PlannerSettingsUpdate): Parsed<{ status: string; message: string }> =>
    parseResponse(schemas.connectionTestResultSchema)(api.post('/api/planner/test-llm', data)),
  listLLMModels: (data: LLMModelListRequest): Parsed<LLMModelListResponse> =>
    parseResponse(schemas.llmModelListResponseSchema)(api.post('/api/planner/llm-models', data)),
  /** Syncs the signed-in user's connected calendars now. */
  syncNow: (): Parsed<SyncResult> => parseResponse(opsSchemas.syncResultSchema)(api.post('/api/planner/sync')),
};

export { connectionsApi } from './connectionsApi';

const rt = runtimeSchemas;
export const mcpApi = {
  getRegistry: (): Parsed<MCPRegistry> => parseResponse(rt.mcpRegistrySchema)(api.get('/api/tools/registry')),
  /** Change a tool's Unattended and/or In chat switch; omitted fields stay, `null` resets one. */
  setToolPolicy: (toolId: string, changes: ToolPolicyChanges): Parsed<ToolPolicy> =>
    parseResponse(rt.toolPolicySchema)(api.put(`/api/tools/policies/${encodeURIComponent(toolId)}`, changes)),
  /** The blanket Unattended / In chat modes (JAN-359). */
  getPolicyModes: (): Parsed<components['schemas']['ToolPolicyModes']> =>
    parseResponse(rt.toolPolicyModesSchema)(api.get('/api/tools/policy-modes')),
  setPolicyModes: (
    changes: components['schemas']['ToolPolicyModesUpdate']
  ): Parsed<components['schemas']['ToolPolicyModes']> =>
    parseResponse(rt.toolPolicyModesSchema)(api.put('/api/tools/policy-modes', changes)),
  listServers: (): Parsed<MCPServer[]> => parseResponse(rt.mcpServerListSchema)(api.get('/api/tools/servers')),
  /** Register a server; `access_token` is a write-only static bearer token (JAN-342). */
  createServer: (data: components['schemas']['MCPServerCreate']): Parsed<MCPServer> => parseResponse(rt.mcpServerSchema)(api.post('/api/tools/servers', data)),
  updateServer: (
    id: number,
    data: Partial<Pick<MCPServer, 'provider_name' | 'endpoint' | 'enabled'>> & Record<string, unknown>
  ): Parsed<MCPServer> => parseResponse(rt.mcpServerSchema)(api.patch(`/api/tools/servers/${id}`, data)),
  discover: (id: number): Parsed<{ server: MCPServer; discovered_count: number }> =>
    parseResponse(rt.mcpDiscoverySchema)(api.post(`/api/tools/servers/${id}/discover`)),
  disconnect: (id: number) => api.delete(`/api/tools/servers/${id}`),
  setToolEnabled: (serverId: number, toolId: number, enabled: boolean): Parsed<MCPTool> =>
    parseResponse(rt.mcpToolSchema)(api.patch(`/api/tools/servers/${serverId}/tools/${toolId}`, { enabled })),
  startOAuth: (id: number): Parsed<{ authorization_url: string }> =>
    parseResponse(rt.mcpOAuthStartSchema)(api.post(`/api/tools/servers/${id}/oauth/start`)),
};

export const characterApi = {
  get: (): Parsed<CharacterProfile> => parseResponse(rt.characterProfileSchema)(api.get('/api/character')),
  update: (data: CharacterProfileUpdate): Parsed<CharacterProfile> =>
    parseResponse(rt.characterProfileSchema)(api.put('/api/character', data)),
  preview: (data: CharacterProfileUpdate): Parsed<CharacterPromptPreview> =>
    parseResponse(rt.characterPromptPreviewSchema)(api.post('/api/character/preview', data)),
};

export const runtimeTriggerApi = {
  list: (params?: RuntimeTriggerQuery): Parsed<RuntimeTriggerPage> =>
    parseResponse(rt.runtimeTriggerPageSchema)(api.get('/api/runtime/triggers/', { params })),
  create: (data: RuntimeTriggerCreate): Parsed<RuntimeTrigger> =>
    parseResponse(rt.runtimeTriggerSchema)(api.post('/api/runtime/triggers/', data)),
  workerStatus: (): Parsed<RuntimeTriggerWorkerStatus> =>
    parseResponse(rt.runtimeTriggerWorkerStatusSchema)(api.get('/api/runtime/triggers/worker')),
};

export const statsApi = {
  get: (days: StatsRangeDays, timezone?: string): Parsed<StatsSummary> =>
    parseResponse(opsSchemas.statsSummarySchema)(api.get('/api/stats', { params: { days, ...(timezone ? { timezone } : {}) } })),
};

const DEFINITIONS = '/api/runtime/definitions';
export const triggerDefinitionApi = {
  list: (timezone?: string): Parsed<TriggerDefinition[]> =>
    parseResponse(rt.triggerDefinitionListSchema)(api.get(`${DEFINITIONS}/`, { params: timezone ? { timezone } : undefined })),
  eventCatalog: (): Parsed<TriggerSubsystemSpec[]> =>
    parseResponse(rt.triggerEventCatalogSchema)(api.get(`${DEFINITIONS}/event-catalog`)),
  create: (data: TriggerDefinitionInput): Parsed<TriggerDefinition> =>
    parseResponse(rt.triggerDefinitionSchema)(api.post(`${DEFINITIONS}/`, data)),
  update: (id: number, data: Partial<TriggerDefinitionInput>): Parsed<TriggerDefinition> =>
    parseResponse(rt.triggerDefinitionSchema)(api.patch(`${DEFINITIONS}/${id}`, data)),
  delete: (id: number) => api.delete(`${DEFINITIONS}/${id}`),
  run: (id: number): Parsed<RuntimeTrigger> => parseResponse(rt.runtimeTriggerSchema)(api.post(`${DEFINITIONS}/${id}/run`)),
  runs: (id: number, limit: number = 10): Parsed<RuntimeTrigger[]> =>
    parseResponse(rt.runtimeTriggerListSchema)(api.get(`${DEFINITIONS}/${id}/runs`, { params: { limit } })),
  /** Automations Janus suggested (JAN-235), newest first; omit `status` for all of them. */
  proposals: (status?: TriggerProposalStatus, limit: number = 50): Parsed<TriggerProposal[]> =>
    parseResponse(rt.triggerProposalListSchema)(api.get(`${DEFINITIONS}/proposals`, { params: { status, limit } })),
  proposalCount: (): Parsed<{ open: number }> =>
    parseResponse(rt.triggerProposalCountSchema)(api.get(`${DEFINITIONS}/proposals/count`)),
  approveProposal: (id: number, data: TriggerProposalApproval): Parsed<TriggerDefinition> =>
    parseResponse(rt.triggerDefinitionSchema)(api.post(`${DEFINITIONS}/${id}/approve`, data)),
  rejectProposal: (id: number, reason?: string): Parsed<TriggerDefinition> =>
    parseResponse(rt.triggerDefinitionSchema)(api.post(`${DEFINITIONS}/${id}/reject`, { reason: reason || null })),
};

export const skillApi = {
  getSkills: () => api.get<SkillSummary[]>('/api/skills'),
  discoverSkills: (query: string, limit: number = 20) =>
    api.get<RemoteSkillSummary[]>('/api/skills/discover', { params: { query, limit } }),
  importSkill: (data: SkillImportRequest) => api.post<SkillDetail>('/api/skills/import', data),
  designAssist: (data: SkillDesignAssistRequest) => api.post<SkillDesignAssistResponse>('/api/skills/design-assist', data),
  getSkill: (id: number) => api.get<SkillDetail>(`/api/skills/${id}`),
  createSkill: (data: SkillCreate) => api.post<SkillDetail>('/api/skills', data),
  updateSkill: (id: number, data: SkillUpdate) => api.put<SkillDetail>(`/api/skills/${id}`, data),
  deleteSkill: (id: number) => api.delete(`/api/skills/${id}`),
};

export const githubApi = {
  getSettings: () => api.get<GitHubSettings>('/api/github/settings'),
  updateSettings: (data: GitHubSettingsUpdate) =>
    api.patch<GitHubSettings>('/api/github/settings', data),
  testConnection: () => api.post<{ status: string; message: string }>('/api/github/test'),
  createIssue: (data: GitHubIssueCreate) =>
    api.post<GitHubIssueResponse>('/api/github/issues', data),
};

export const linearApi = {
  getSettings: () => api.get<LinearSettings>('/api/linear/settings'),
  updateSettings: (data: LinearSettingsUpdate) =>
    api.patch<LinearSettings>('/api/linear/settings', data),
  testConnection: () => api.post<{ status: string; message: string }>('/api/linear/test'),
  createIssue: (data: LinearIssueCreate) =>
    api.post<LinearIssueResponse>('/api/linear/issues', data),
};

export const webSearchApi = {
  getSettings: () => api.get<WebSearchSettings>('/api/web-search/settings'),
  updateSettings: (data: WebSearchSettingsUpdate) =>
    api.patch<WebSearchSettings>('/api/web-search/settings', data),
};

const ks = knowledgeSchemas;
export const conversationApi = {
  list: (): Parsed<ConversationSummary[]> => parseResponse(ks.conversationSummaryListSchema)(api.get('/api/conversations/')),
  create: (title?: string): Parsed<ConversationSummary> =>
    parseResponse(ks.conversationSummarySchema)(api.post('/api/conversations/', { title: title ?? null })),
  get: (id: number): Parsed<ConversationDetail> => parseResponse(ks.conversationDetailSchema)(api.get(`/api/conversations/${id}`)),
  delete: (id: number) => api.delete(`/api/conversations/${id}`),
};

export const knowledgeApi = {
  listSources: (params?: { sourceType?: KnowledgeSourceType; status?: KnowledgeSourceStatus; limit?: number }): Parsed<KnowledgeSource[]> =>
    parseResponse(ks.knowledgeSourceListSchema)(
      api.get('/api/knowledge/sources', {
        params: { source_type: params?.sourceType, status: params?.status, limit: params?.limit ?? 200 },
      })
    ),
  upload: (file: File): Parsed<KnowledgeSource> => {
    const form = new FormData();
    form.append('file', file);
    return parseResponse(ks.knowledgeSourceSchema)(api.post('/api/knowledge/uploads', form));
  },
  deleteSource: (id: number) => api.delete<void>(`/api/knowledge/sources/${id}`),
  getExtraction: (): Parsed<KnowledgeExtractionStatus> =>
    parseResponse(ks.knowledgeExtractionStatusSchema)(api.get('/api/knowledge/extraction')),
  updateExtraction: (payload: { enabled?: boolean; monthly_token_budget?: number }): Parsed<KnowledgeExtractionStatus> =>
    parseResponse(ks.knowledgeExtractionStatusSchema)(api.put('/api/knowledge/extraction', payload)),
  listEntities: (params?: { entityType?: KnowledgeEntityType; query?: string; limit?: number }): Parsed<KnowledgeEntity[]> =>
    parseResponse(ks.knowledgeEntityListSchema)(
      api.get('/api/knowledge/entities', {
        params: { entity_type: params?.entityType, q: params?.query || undefined, limit: params?.limit ?? 200 },
      })
    ),
  listMergeSuggestions: (): Parsed<KnowledgeMergeSuggestion[]> =>
    parseResponse(ks.knowledgeMergeSuggestionListSchema)(api.get('/api/knowledge/merge-suggestions')),
  acceptMerge: (id: number): Parsed<KnowledgeEntity> =>
    parseResponse(ks.knowledgeEntitySchema)(api.post(`/api/knowledge/merge-suggestions/${id}/accept`)),
  rejectMerge: (id: number) => api.post<void>(`/api/knowledge/merge-suggestions/${id}/reject`),
  search: (params: { query: string; sourceTypes?: KnowledgeSourceType[]; limit?: number; hops?: number }): Parsed<KnowledgeSearchResult> =>
    parseResponse(ks.knowledgeSearchResultSchema)(
      api.get('/api/knowledge/search', {
        params: { q: params.query, source_type: params.sourceTypes, limit: params.limit, hops: params.hops },
        paramsSerializer: { indexes: null },
      })
    ),
  getEntity: (id: number): Parsed<KnowledgeEntityDetail> =>
    parseResponse(ks.knowledgeEntityDetailSchema)(api.get(`/api/knowledge/entities/${id}`)),
};

const wikiPage = (request: Promise<AxiosResponse<unknown>>): Parsed<WikiPageDetail> => parseResponse(ks.wikiPageDetailSchema)(request);
export const wikiApi = {
  listPages: (params?: { subjectType?: 'topic' | 'entity'; subjectId?: number; query?: string }): Parsed<WikiPageSummary[]> =>
    parseResponse(ks.wikiPageSummaryListSchema)(
      api.get('/api/wiki/pages', {
        params: { subject_type: params?.subjectType, subject_id: params?.subjectId, q: params?.query || undefined },
      })
    ),
  getPage: (id: number) => wikiPage(api.get(`/api/wiki/pages/${id}`)),
  createPage: (subjectType: 'topic' | 'entity', subjectId: number) =>
    wikiPage(api.post('/api/wiki/pages', { subject_type: subjectType, subject_id: subjectId })),
  editPage: (id: number, body: string) => wikiPage(api.put(`/api/wiki/pages/${id}`, { body })),
  acceptRevision: (id: number) => wikiPage(api.post(`/api/wiki/revisions/${id}/accept`)),
  dismissRevision: (id: number) => api.post<void>(`/api/wiki/revisions/${id}/dismiss`),
  restoreRevision: (id: number) => wikiPage(api.post(`/api/wiki/revisions/${id}/restore`)),
  getSynthesis: (): Parsed<WikiSynthesisStatus> => parseResponse(ks.wikiSynthesisStatusSchema)(api.get('/api/wiki/synthesis')),
  updateSynthesis: (enabled: boolean): Parsed<WikiSynthesisStatus> =>
    parseResponse(ks.wikiSynthesisStatusSchema)(api.put('/api/wiki/synthesis', { enabled })),
};

export const memoryApi = {
  list: (params?: { query?: string; includeArchived?: boolean; limit?: number }): Parsed<AgentMemoryFact[]> =>
    parseResponse(ks.memoryFactListSchema)(
      api.get('/api/memory/', {
        params: { query: params?.query || undefined, include_archived: params?.includeArchived ?? false, limit: params?.limit ?? 100 },
      })
    ),
  create: (data: { summary: string; detail?: string | null }): Parsed<AgentMemoryFact> =>
    parseResponse(ks.memoryFactSchema)(api.post('/api/memory/', data)),
  /** Edits a fact's summary and/or detail; an empty `detail` clears it (JAN-224). */
  update: (id: number, data: { summary?: string; detail?: string | null }): Parsed<AgentMemoryFact> =>
    parseResponse(ks.memoryFactSchema)(api.patch(`/api/memory/${id}`, data)),
  compact: (): Parsed<AgentMemoryCompactionRun> => parseResponse(ks.memoryCompactionRunSchema)(api.post('/api/memory/compact')),
  getLatestCompactionRun: (): Parsed<AgentMemoryCompactionRun | null> =>
    parseResponse(ks.latestMemoryCompactionRunSchema)(api.get('/api/memory/compact/latest')),
  delete: (id: number) => api.delete<void>(`/api/memory/${id}`),
};

export const topicApi = {
  list: (params?: { query?: string; status?: string; limit?: number }): Parsed<TopicSummary[]> =>
    parseResponse(schemas.topicSummaryListSchema)(
      api.get('/api/topics/', {
        params: { query: params?.query || undefined, status: params?.status || undefined, limit: params?.limit ?? 50 },
      })
    ),
  create: (data: TopicCreate): Parsed<TopicSummary> => parseResponse(schemas.topicSummarySchema)(api.post('/api/topics/', data)),
  get: (id: number): Parsed<TopicDetail> => parseResponse(ks.topicDetailSchema)(api.get(`/api/topics/${id}`)),
  update: (id: number, data: TopicUpdate): Parsed<TopicSummary> =>
    parseResponse(schemas.topicSummarySchema)(api.patch(`/api/topics/${id}`, data)),
  delete: (id: number) => api.delete<void>(`/api/topics/${id}`),
};

export const docsApi = {
  getNav: async () => {
    const response = await api.get<DocNavItem[]>('/api/docs/nav');
    return { data: response.data };
  },
  getPage: async (path: string) => {
    const response = await api.get<string>(`/api/docs/${path}`);
    return { data: response.data };
  },
};

export const statusApi = {
  getSummary: () =>
    parseResponse(schemas.statusOverviewSchema)(api.get('/api/status/summary')) as Promise<{ data: StatusOverview }>,
  getHealth: () => parseResponse(schemas.healthSchema)(api.get('/api/status/health')),
  /** The running version and the release notes after `since` (the last version the user saw). */
  getVersion: (since?: string | null) =>
    parseResponse(schemas.versionInfoSchema)(
      api.get('/api/status/version', { params: since ? { since } : undefined }),
    ),
};

export default api;
