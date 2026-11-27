import axios from 'axios';
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiValidationError } from './lib/apiValidation';

/**
 * Runtime response validation of the API client (JAN-142), exercised through the real
 * `api.ts` wrappers with a stubbed axios adapter. Other test files mock `./api` (and the
 * module registry is shared, `isolate: false`), so the real module is loaded explicitly.
 */
type ApiModule = typeof import('./api');
let apiModule: ApiModule;

let nextBody: unknown;
const requests: Array<{ method: string; url: string }> = [];

const respond: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
  if (config.url === '/api/auth/csrf') {
    return { data: { csrf_token: 'csrf' }, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse;
  }
  requests.push({ method: (config.method ?? 'get').toUpperCase(), url: config.url ?? '' });
  return { data: nextBody, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse;
};

let originalClientAdapter: unknown;
let originalGlobalAdapter: unknown;

beforeAll(async () => {
  apiModule = await vi.importActual<ApiModule>('./api');
  originalClientAdapter = apiModule.default.defaults.adapter;
  originalGlobalAdapter = axios.defaults.adapter;
  apiModule.default.defaults.adapter = respond;
  // The CSRF token for mutations is fetched with the global axios instance.
  axios.defaults.adapter = respond;
});

afterAll(() => {
  apiModule.default.defaults.adapter = originalClientAdapter as AxiosAdapter;
  axios.defaults.adapter = originalGlobalAdapter as AxiosAdapter;
});

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  requests.length = 0;
  nextBody = undefined;
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  consoleError.mockRestore();
});

const user = { id: 1, username: 'ada', is_admin: true, last_login: null, google_linked: false, ui_version: 'v2' };
const emailAccount = {
  id: 3,
  name: 'Work',
  email: 'ada@example.com',
  imap_server: 'imap.example.com',
  imap_port: 993,
  username: 'ada',
  is_active: true,
};
const rule = { id: 5, name: 'Boss', pattern: 'boss@', field: 'sender', priority: 'important', is_active: true, order: 0 };
const email = {
  id: 11,
  message_id: '<m@x>',
  sender: 'boss@example.com',
  subject: 'Q4',
  snippet: 'Numbers',
  body: 'Full text',
  priority: 'spam',
  received_at: '2026-10-01T09:00:00',
  account_id: 3,
  topics: [],
};
const plannerAccount = { id: 2, name: 'Google Calendar', provider: 'google', api_url: null, is_active: true };
const task = {
  id: 21,
  kind: 'task',
  source: 'manual',
  source_label: 'Manual',
  read_only: false,
  title: 'Pay rent',
  status: 'todo',
};
const event = {
  id: 31,
  kind: 'event',
  source: 'manual',
  source_label: 'Manual',
  read_only: false,
  title: 'Dentist',
  start_time: '2026-10-12T09:00:00',
  end_time: '2026-10-12T10:00:00',
  all_day: false,
};

/** Asserts that the call rejects with an ApiValidationError naming these field paths. */
const expectInvalid = async (call: () => Promise<unknown>, method: string, endpoint: string, paths: string[]) => {
  const error = await call().then(
    () => {
      throw new Error('expected the response to be rejected');
    },
    (reason: unknown) => reason
  );
  expect(error).toBeInstanceOf(ApiValidationError);
  const validationError = error as ApiValidationError;
  expect(validationError.method).toBe(method);
  expect(validationError.endpoint).toBe(endpoint);
  expect(validationError.issues.map((issue) => issue.path)).toEqual(paths);
  expect(consoleError).toHaveBeenCalledWith(
    `[api] Response validation failed: ${method} ${endpoint}`,
    expect.objectContaining({ endpoint, issues: validationError.issues })
  );
};

describe('auth/session responses', () => {
  it('parses the current user and keeps fields the schema does not list', async () => {
    nextBody = { ...user, future_flag: true };
    const response = await apiModule.authApi.getMe();
    expect(response.data).toEqual({ ...user, future_flag: true });
  });

  it('falls back to the default shell for a ui_version this build does not know', async () => {
    nextBody = { ...user, ui_version: 'v9' };
    const response = await apiModule.authApi.updateUIPreference('v2');
    expect(response.data.ui_version).toBeUndefined();
    expect(response.data.username).toBe('ada');
    expect(requests).toEqual([{ method: 'PATCH', url: '/api/auth/me/ui-preference' }]);
  });

  it('rejects a user without an id or admin flag', async () => {
    nextBody = { username: 'ada', is_admin: 'yes', google_linked: false };
    await expectInvalid(() => apiModule.authApi.getMe(), 'GET', '/api/auth/me', ['id', 'is_admin']);
  });

  it('rejects an HTML page served instead of JSON', async () => {
    nextBody = '<!doctype html><title>Sign in</title>';
    await expectInvalid(() => apiModule.authApi.getMe(), 'GET', '/api/auth/me', ['(root)']);
  });

  it('validates the password-change acknowledgement', async () => {
    nextBody = { status: 'success' };
    await expect(
      apiModule.authApi.changePassword({ current_password: 'a', new_password: 'b' })
    ).resolves.toMatchObject({ data: { status: 'success' } });

    nextBody = {};
    await expectInvalid(
      () => apiModule.authApi.changePassword({ current_password: 'a', new_password: 'b' }),
      'POST',
      '/api/auth/change-password',
      ['status']
    );
  });
});

describe('email account and rule responses', () => {
  it('parses accounts, a saved account and a connection test', async () => {
    nextBody = [emailAccount];
    await expect(apiModule.emailApi.getAccounts()).resolves.toMatchObject({ data: [emailAccount] });

    nextBody = emailAccount;
    await expect(
      apiModule.emailApi.addAccount({ ...emailAccount, password: 'pw' })
    ).resolves.toMatchObject({ data: emailAccount });
    await expect(apiModule.emailApi.updateAccount(3, { name: 'Work' })).resolves.toMatchObject({ data: emailAccount });

    nextBody = { status: 'error', message: 'Connection refused' };
    await expect(apiModule.emailApi.testAccount(3)).resolves.toMatchObject({
      data: { status: 'error', message: 'Connection refused' },
    });
  });

  it('rejects an account list whose entries lost their port or id', async () => {
    nextBody = [{ ...emailAccount, imap_port: '993' }, { ...emailAccount, id: undefined }];
    await expectInvalid(() => apiModule.emailApi.getAccounts(), 'GET', '/api/emails/accounts', ['0.imap_port', '1.id']);
  });

  it('rejects a saved account that is not an account (mutation success state)', async () => {
    nextBody = { detail: 'ok' };
    await expectInvalid(
      () => apiModule.emailApi.addAccount({ ...emailAccount, password: 'pw' }),
      'POST',
      '/api/emails/accounts',
      ['id', 'name', 'email', 'imap_server', 'imap_port', 'username', 'is_active']
    );
  });

  it('rejects a connection test without a message', async () => {
    nextBody = { status: 'success' };
    await expectInvalid(() => apiModule.emailApi.testAccount(3), 'POST', '/api/emails/accounts/3/test', ['message']);
  });

  it('parses rules and rule mutations, and rejects an unknown priority', async () => {
    nextBody = [rule];
    await expect(apiModule.emailApi.getRules()).resolves.toMatchObject({ data: [rule] });
    nextBody = rule;
    await expect(apiModule.emailApi.addRule({ ...rule })).resolves.toMatchObject({ data: rule });
    await expect(apiModule.emailApi.updateRule(5, { order: 1 })).resolves.toMatchObject({ data: rule });

    nextBody = { ...rule, priority: 'urgent' };
    await expectInvalid(() => apiModule.emailApi.updateRule(5, { order: 1 }), 'PATCH', '/api/emails/rules/5', [
      'priority',
    ]);
  });

  it('parses a reclassified email, keeping its body, and drops the query string from errors', async () => {
    nextBody = email;
    await expect(apiModule.emailApi.updatePriority(11, 'spam')).resolves.toMatchObject({ data: { body: 'Full text' } });

    nextBody = { ...email, priority: 'unknown' };
    await expectInvalid(() => apiModule.emailApi.updatePriority(11, 'spam'), 'PATCH', '/api/emails/11/priority', [
      'priority',
    ]);
  });
});

describe('planner and task responses', () => {
  it('parses planner accounts and rejects a list that is not one', async () => {
    nextBody = [plannerAccount];
    await expect(apiModule.plannerApi.getAccounts()).resolves.toMatchObject({ data: [plannerAccount] });

    nextBody = { accounts: [plannerAccount] };
    await expectInvalid(() => apiModule.plannerApi.getAccounts(), 'GET', '/api/planner/accounts', ['(root)']);
  });

  it('parses created and updated local tasks and events', async () => {
    nextBody = task;
    await expect(apiModule.plannerApi.createLocalTask({ title: 'Pay rent' })).resolves.toEqual({ data: task });
    await expect(apiModule.plannerApi.updateLocalTask(21, { status: 'done' })).resolves.toEqual({ data: task });
    nextBody = event;
    await expect(
      apiModule.plannerApi.createLocalEvent({ title: 'Dentist', start_time: event.start_time, end_time: event.end_time })
    ).resolves.toEqual({ data: event });
    await expect(apiModule.plannerApi.updateLocalEvent(31, { title: 'Dentist' })).resolves.toEqual({ data: event });
  });

  it('rejects a created task that came back as an event', async () => {
    nextBody = event;
    await expectInvalid(() => apiModule.plannerApi.createLocalTask({ title: 'Pay rent' }), 'POST', '/api/planner/local/tasks', [
      'kind',
      'status',
    ]);
  });

  it('rejects an updated event without its times', async () => {
    nextBody = { ...event, start_time: undefined, end_time: 1 };
    await expectInvalid(
      () => apiModule.plannerApi.updateLocalEvent(31, { title: 'x' }),
      'PATCH',
      '/api/planner/local/events/31',
      ['start_time', 'end_time']
    );
  });

  it('parses the LLM connection test and the model listing', async () => {
    nextBody = { status: 'error', message: 'Ollama is not reachable' };
    await expect(apiModule.plannerApi.testLLM({})).resolves.toMatchObject({ data: { status: 'error' } });

    nextBody = { models: [{ id: 'llama3', label: 'llama3' }], error: null };
    await expect(apiModule.plannerApi.listLLMModels({ provider: 'ollama' })).resolves.toMatchObject({
      data: { models: [{ id: 'llama3', label: 'llama3' }] },
    });
  });

  it('rejects a model listing whose models are plain strings', async () => {
    nextBody = { models: ['llama3'] };
    await expectInvalid(() => apiModule.plannerApi.listLLMModels({ provider: 'ollama' }), 'POST', '/api/planner/llm-models', [
      'models.0',
    ]);
  });
});

describe('previously validated dashboard responses', () => {
  it('now fail as ApiValidationError too', async () => {
    nextBody = [{ id: 'x' }];
    await expectInvalid(() => apiModule.dashboardApi.getTasks(), 'GET', '/api/dashboard/tasks', [
      '0.id',
      '0.kind',
      '0.source',
      '0.source_label',
      '0.read_only',
      '0.title',
      '0.status',
    ]);
  });
});

// ---------------------------------------------------------------------------
// JAN-335: the remaining endpoint groups. Each group parses a valid answer and
// rejects a malformed one with the field paths that drifted.
// ---------------------------------------------------------------------------

const topic = {
  id: 7,
  title: 'Kitchen renovation',
  summary: null,
  marker_keywords: ['kitchen'],
  status: 'active',
  pinned: false,
  user_confirmed: true,
  origin: 'manual',
  confidence: 'high',
  first_seen_at: '2026-10-01T09:00:00',
  last_seen_at: '2026-10-02T09:00:00',
  created_at: '2026-10-01T09:00:00',
  updated_at: '2026-10-02T09:00:00',
  evidence_count: 1,
};
const fact = {
  id: 2,
  summary: 'Prefers mornings',
  detail: null,
  source: 'user',
  status: 'active',
  source_conversation_id: null,
  compacted_into_id: null,
  created_at: '2026-10-01T09:00:00',
  updated_at: '2026-10-01T09:00:00',
  last_accessed_at: null,
};
const compactionRun = {
  id: 1,
  status: 'completed',
  result_summary: 'Merged 2 facts',
  error_message: null,
  facts_considered: 4,
  facts_archived: 2,
  facts_created: 1,
  created_at: '2026-10-01T09:00:00',
  started_at: '2026-10-01T09:00:01',
  completed_at: '2026-10-01T09:00:05',
};
const conversation = {
  id: 12,
  title: 'Weekly plan',
  created_at: '2026-10-01T09:00:00',
  updated_at: '2026-10-01T09:05:00',
  origin: 'janus',
  unread: true,
};
const toolCall = {
  call_id: 'c1',
  tool_id: 'mail',
  tool_name: 'search_mail',
  phase: 'end',
  args: { query: 'x' },
  args_truncated: false,
  result_preview: '2 results',
  result_truncated: false,
  duration_ms: 40,
  approval: null,
};
const knowledgeEntity = {
  id: 3,
  entity_type: 'person',
  name: 'Ada',
  aliases: ['A. L.'],
  description: null,
  mention_count: 2,
  relation_count: 1,
};
const knowledgeSource = {
  id: 8,
  source_type: 'upload',
  owner_id: null,
  part: 0,
  title: 'notes.txt',
  mime_type: 'text/plain',
  size_bytes: 120,
  status: 'ready',
  chunk_count: 1,
  created_at: '2026-10-01T09:00:00',
};
const wikiSummary = {
  id: 5,
  slug: 'kitchen-renovation',
  title: 'Kitchen renovation',
  subject_type: 'topic',
  subject_id: 7,
  user_edited: false,
  synthesized_at: null,
  updated_at: '2026-10-01T09:00:00',
  pending_proposals: 0,
};
const wikiDetail = {
  ...wikiSummary,
  body: 'Tiles ordered [1]. Prefers mornings [2].',
  citations: [
    { n: 1, chunk_id: 4, source_id: 8, source_type: 'upload', title: 'notes.txt' },
    { n: 2, fact_id: 2, source_type: 'memory_fact', title: 'Prefers mornings' },
  ],
  proposals: [],
  revisions: [
    { id: 1, body: 'Tiles ordered.', citations: [], author: 'janus', kind: 'published', created_at: '2026-10-01T09:00:00' },
  ],
};
const synthesis = {
  enabled: true,
  definition_id: 3,
  model: 'llama3',
  is_cloud: false,
  monthly_token_budget: 100000,
  period: '2026-10',
  tokens_used: 10,
};

describe('topic, memory and conversation responses', () => {
  it('parses topics and a topic detail', async () => {
    nextBody = [topic];
    await expect(apiModule.topicApi.list()).resolves.toMatchObject({ data: [topic] });
    nextBody = { ...topic, evidence: [{ id: 1, source_type: 'email', source_id: 3, evidence_role: 'trigger', created_at: topic.created_at }] };
    await expect(apiModule.topicApi.get(7)).resolves.toMatchObject({ data: { evidence: [{ source_type: 'email' }] } });
  });

  it('rejects a topic with an unknown status and evidence without its role', async () => {
    nextBody = { ...topic, status: 'archived' };
    await expectInvalid(() => apiModule.topicApi.update(7, { pinned: true }), 'PATCH', '/api/topics/7', ['status']);
    nextBody = { ...topic, evidence: [{ id: 1, source_type: 'email', created_at: topic.created_at }] };
    await expectInvalid(() => apiModule.topicApi.get(7), 'GET', '/api/topics/7', ['evidence.0.evidence_role']);
  });

  it('parses memory facts, a compaction run and the empty latest run', async () => {
    nextBody = [fact];
    await expect(apiModule.memoryApi.list()).resolves.toMatchObject({ data: [fact] });
    nextBody = compactionRun;
    await expect(apiModule.memoryApi.compact()).resolves.toMatchObject({ data: compactionRun });
    nextBody = null;
    await expect(apiModule.memoryApi.getLatestCompactionRun()).resolves.toMatchObject({ data: null });
  });

  it('rejects a memory fact without its summary and a run with an unknown status', async () => {
    nextBody = { ...fact, summary: undefined };
    await expectInvalid(() => apiModule.memoryApi.create({ summary: 'x' }), 'POST', '/api/memory/', ['summary']);
    nextBody = { ...compactionRun, status: 'done' };
    await expectInvalid(() => apiModule.memoryApi.getLatestCompactionRun(), 'GET', '/api/memory/compact/latest', ['status']);
  });

  it('parses Janus-started conversations and a conversation with stored tool calls', async () => {
    nextBody = [conversation, { ...conversation, id: 13, origin: 'user', unread: false }];
    await expect(apiModule.conversationApi.list()).resolves.toMatchObject({ data: [{ origin: 'janus', unread: true }, {}] });
    nextBody = {
      ...conversation,
      messages: [{ id: 1, role: 'assistant', content: 'Done', created_at: conversation.created_at, tool_calls: [toolCall], ui_blocks: [] }],
    };
    const response = await apiModule.conversationApi.get(12);
    expect(response.data.messages[0].tool_calls).toEqual([toolCall]);
    // Fields the schema does not list (UI blocks) pass through for chatFrames to read.
    expect(response.data.messages[0]).toHaveProperty('ui_blocks', []);
  });

  it('rejects an unknown conversation origin and a stored tool call with an unknown phase', async () => {
    nextBody = [{ ...conversation, origin: 'trigger' }];
    await expectInvalid(() => apiModule.conversationApi.list(), 'GET', '/api/conversations/', ['0.origin']);
    nextBody = { ...conversation, messages: [{ id: 1, role: 'assistant', content: '', created_at: '', tool_calls: [{ ...toolCall, phase: 'done' }] }] };
    await expectInvalid(() => apiModule.conversationApi.get(12), 'GET', '/api/conversations/12', ['messages.0.tool_calls.0.phase']);
  });
});

describe('knowledge, wiki and knowledge stack responses', () => {
  it('parses sources, entities, merge suggestions, search and an entity detail', async () => {
    nextBody = [knowledgeSource];
    await expect(apiModule.knowledgeApi.listSources()).resolves.toMatchObject({ data: [knowledgeSource] });
    nextBody = [knowledgeEntity];
    await expect(apiModule.knowledgeApi.listEntities()).resolves.toMatchObject({ data: [knowledgeEntity] });
    nextBody = [{ id: 1, reason: 'Same name', created_at: '2026-10-01', entity: knowledgeEntity, duplicate: { ...knowledgeEntity, id: 4 } }];
    await expect(apiModule.knowledgeApi.listMergeSuggestions()).resolves.toMatchObject({ data: [{ reason: 'Same name' }] });
    const ref = { id: 3, name: 'Ada', type: 'person' };
    nextBody = {
      hits: [{ chunk_id: 1, text: 'Ada', source: { id: 8, type: 'upload', title: 'notes.txt' }, entities: [ref], matched_by: ['keyword'] }],
      entities: [ref],
    };
    await expect(apiModule.knowledgeApi.search({ query: 'Ada' })).resolves.toMatchObject({ data: { entities: [ref] } });
    nextBody = {
      ...ref,
      aliases: [],
      relations: [{ id: 1, direction: 'outgoing', predicate: 'works_at', other: { id: 9, name: 'ACME', type: 'organization' }, confidence: 'high', supporting_chunks: 2 }],
      sources: [{ id: 8, type: 'upload', mentions: 2 }],
    };
    await expect(apiModule.knowledgeApi.getEntity(3)).resolves.toMatchObject({ data: { relations: [{ predicate: 'works_at' }] } });
    nextBody = { enabled: false, model: 'llama3', is_cloud: false, monthly_token_budget: 1, period: '2026-10', tokens_used: 0, calls: 0, pending_chunks: 0 };
    await expect(apiModule.knowledgeApi.getExtraction()).resolves.toMatchObject({ data: { enabled: false } });
  });

  it('rejects an unknown source type, an entity without aliases and a hit without its source', async () => {
    nextBody = [{ ...knowledgeSource, source_type: 'fax' }];
    await expectInvalid(() => apiModule.knowledgeApi.listSources(), 'GET', '/api/knowledge/sources', ['0.source_type']);
    nextBody = { ...knowledgeEntity, aliases: null };
    await expectInvalid(() => apiModule.knowledgeApi.acceptMerge(1), 'POST', '/api/knowledge/merge-suggestions/1/accept', ['aliases']);
    nextBody = { hits: [{ chunk_id: 1, text: 'Ada', entities: [], matched_by: [] }], entities: [] };
    await expectInvalid(() => apiModule.knowledgeApi.search({ query: 'Ada' }), 'GET', '/api/knowledge/search', ['hits.0.source']);
  });

  it('parses wiki pages, including a cited memory fact, and the synthesis status', async () => {
    nextBody = [wikiSummary];
    await expect(apiModule.wikiApi.listPages()).resolves.toMatchObject({ data: [wikiSummary] });
    nextBody = wikiDetail;
    const page = await apiModule.wikiApi.getPage(5);
    expect(page.data.citations[1]).toMatchObject({ fact_id: 2, source_type: 'memory_fact' });
    await expect(apiModule.wikiApi.editPage(5, 'x')).resolves.toMatchObject({ data: { id: 5 } });
    nextBody = synthesis;
    await expect(apiModule.wikiApi.updateSynthesis(true)).resolves.toMatchObject({ data: synthesis });
  });

  it('rejects a wiki page with an unknown revision author and a synthesis status without its model', async () => {
    nextBody = { ...wikiDetail, revisions: [{ ...wikiDetail.revisions[0], author: 'robot' }] };
    await expectInvalid(() => apiModule.wikiApi.acceptRevision(1), 'POST', '/api/wiki/revisions/1/accept', ['revisions.0.author']);
    nextBody = { ...synthesis, model: undefined };
    await expectInvalid(() => apiModule.wikiApi.getSynthesis(), 'GET', '/api/wiki/synthesis', ['model']);
  });

  it('parses page provenance and the knowledge graph, and rejects an unknown node kind', async () => {
    const { knowledgeStackApi } = await vi.importActual<typeof import('./lib/knowledgeStack')>('./lib/knowledgeStack');
    const { sampleGraph, sampleProvenance } = await import('./components/features/knowledgeStackFixtures');
    nextBody = sampleProvenance();
    await expect(knowledgeStackApi.getProvenance(5)).resolves.toMatchObject({ data: { page_id: sampleProvenance().page_id } });
    nextBody = sampleGraph();
    await expect(knowledgeStackApi.getGraph()).resolves.toMatchObject({ data: { nodes: sampleGraph().nodes } });

    const graph = sampleGraph();
    nextBody = { ...graph, nodes: [{ ...graph.nodes[0], kind: 'fact' }] };
    await expectInvalid(() => knowledgeStackApi.getGraph(), 'GET', '/api/knowledge/graph', ['nodes.0.kind']);
  });
});

const runtimeRun = {
  id: 40,
  definition_id: 6,
  trigger_id: 'trg-1',
  task_id: 'task-1',
  kind: 'manual',
  source: 'definition',
  status: 'completed',
  created_at: '2026-10-01T09:00:00',
  claimed_at: null,
  completed_at: '2026-10-01T09:01:00',
  last_error: null,
  attempt_count: 1,
  next_attempt_at: null,
  result: 'Done',
  definition_name: 'Morning briefing',
  definition_origin: 'system',
};
const definition = {
  id: 6,
  builtin_key: null,
  name: 'Weekly review',
  objective: 'Summarise the week',
  tool_ids: ['mail'],
  kind: 'schedule',
  schedule: { type: 'daily', time: '07:00', days: [0, 4] },
  timezone: 'Europe/Berlin',
  event_type: null,
  enabled: true,
  origin: 'user',
  llm_provider: null,
  llm_model: null,
  next_run_at: '2026-10-13T05:00:00Z',
  last_run_at: null,
  created_at: '2026-10-01T09:00:00',
  updated_at: '2026-10-01T09:00:00',
};
const proposal = {
  ...definition,
  origin: 'agent',
  enabled: false,
  proposal_status: 'proposed',
  proposal_rationale: 'You asked for it',
  proposal_source: { conversation_id: 12, untrusted_context: ['mail'], injection_suspected: false },
  blocked_tool_ids: ['mail'],
  untrusted_context: ['mail'],
  injection_suspected: false,
  next_occurrence: '2026-10-13T05:00:00Z',
  supersedes: null,
};
const mcpServer = {
  id: 2,
  provider_name: 'Docs',
  endpoint: 'https://mcp.example.com',
  transport: 'streamable_http',
  enabled: true,
  discovery_status: 'ready',
  last_health_status: null,
  last_health_at: null,
  last_error: null,
  oauth_required: false,
  oauth_configured: false,
  credential_configured: false,
  credential_status: 'none',
  tools: [
    {
      id: 1,
      upstream_name: 'search',
      namespaced_id: 'mcp.docs.search',
      description: 'Search docs',
      input_schema: { type: 'object' },
      enabled: true,
      created_at: '2026-10-01T09:00:00',
      updated_at: '2026-10-01T09:00:00',
    },
  ],
  created_at: '2026-10-01T09:00:00',
  updated_at: '2026-10-01T09:00:00',
};
const toolPolicy = {
  tool_id: 'mail',
  unattended_allowed: true,
  default_unattended_allowed: false,
  chat_mode: 'ask',
  default_chat_mode: 'allowed',
  overridden: true,
  locked_reason: null,
  effective_unattended_allowed: true,
  effective_chat_asks: true,
  withheld_unattended_tools: [],
};
const builtin = {
  id: 'mail',
  display_name: 'Mail',
  description: 'Read mail',
  required_capabilities: ['mail'],
  operation: 'read',
  risk_level: 'low',
  confirmation: 'none',
  docs_ref: null,
  source: 'builtin',
  unattended_allowed: true,
  chat_mode: 'allowed',
  withheld_unattended_tools: [],
  connected: true,
};
const characterProfile = {
  constitution_id: 'janus-core',
  version: 2,
  name: 'Janus',
  identity: 'A personal agent',
  core_values: ['privacy'],
  non_negotiables: ['no secrets'],
  traits: [{ name: 'warmth', baseline: 0.5, max_delta: 0.1, description: '' }],
  additional_instructions: null,
  memory_instructions: null,
  created_at: null,
  updated_at: null,
};

describe('runtime trigger, definition and proposal responses', () => {
  it('parses a run page, a created run, the worker status and a definition run list', async () => {
    nextBody = { items: [runtimeRun], total: 1, limit: 25, offset: 0, has_more: false };
    await expect(apiModule.runtimeTriggerApi.list()).resolves.toMatchObject({ data: { items: [runtimeRun] } });
    nextBody = { ...runtimeRun, status: 'queued', output: 'extra' };
    await expect(
      apiModule.runtimeTriggerApi.create({ task_id: 't', kind: 'manual', objective: 'x' })
    ).resolves.toMatchObject({ data: { status: 'queued', output: 'extra' } });
    nextBody = { enabled: true, running: false };
    await expect(apiModule.runtimeTriggerApi.workerStatus()).resolves.toMatchObject({ data: { running: false } });
    nextBody = [runtimeRun];
    await expect(apiModule.triggerDefinitionApi.runs(6)).resolves.toMatchObject({ data: [runtimeRun] });
  });

  it('rejects a run with an unknown status and a page without its paging fields', async () => {
    nextBody = { ...runtimeRun, status: 'paused' };
    await expectInvalid(() => apiModule.triggerDefinitionApi.run(6), 'POST', '/api/runtime/definitions/6/run', ['status']);
    nextBody = { items: [] };
    await expectInvalid(() => apiModule.runtimeTriggerApi.list(), 'GET', '/api/runtime/triggers/', [
      'total',
      'limit',
      'offset',
      'has_more',
    ]);
  });

  it('parses definitions, the event catalog, proposals and the open-proposal count', async () => {
    nextBody = [definition, { ...definition, id: 7, kind: 'event', schedule: null, event_type: 'mail.received' }];
    await expect(apiModule.triggerDefinitionApi.list('Europe/Berlin')).resolves.toMatchObject({ data: [definition, { id: 7 }] });
    nextBody = [
      {
        subsystem: 'mail',
        label: 'Mail',
        description: 'Mail events',
        events: [{ event_type: 'mail.received', label: 'Mail received', description: 'New mail', payload: [{ name: 'email_id', description: 'Id' }] }],
      },
    ];
    await expect(apiModule.triggerDefinitionApi.eventCatalog()).resolves.toMatchObject({ data: [{ subsystem: 'mail' }] });
    nextBody = [proposal];
    await expect(apiModule.triggerDefinitionApi.proposals('proposed')).resolves.toMatchObject({ data: [proposal] });
    nextBody = { open: 2 };
    await expect(apiModule.triggerDefinitionApi.proposalCount()).resolves.toMatchObject({ data: { open: 2 } });
    nextBody = { ...definition, proposal_status: 'approved' };
    await expect(apiModule.triggerDefinitionApi.approveProposal(6, { enable: true })).resolves.toMatchObject({
      data: { proposal_status: 'approved' },
    });
  });

  it('rejects a definition with an unknown schedule type, a proposal without its review fields, and a negative count', async () => {
    nextBody = { ...definition, schedule: { type: 'weekly' } };
    await expectInvalid(() => apiModule.triggerDefinitionApi.update(6, { name: 'x' }), 'PATCH', '/api/runtime/definitions/6', [
      'schedule.type',
    ]);
    nextBody = [{ ...definition, proposal_status: 'proposed' }];
    await expectInvalid(() => apiModule.triggerDefinitionApi.proposals(), 'GET', '/api/runtime/definitions/proposals', [
      '0.blocked_tool_ids',
      '0.untrusted_context',
      '0.injection_suspected',
      '0.next_occurrence',
      '0.supersedes',
    ]);
    nextBody = { open: -1 };
    await expectInvalid(() => apiModule.triggerDefinitionApi.proposalCount(), 'GET', '/api/runtime/definitions/proposals/count', [
      'open',
    ]);
  });
});

describe('tool registry, policy and MCP server responses', () => {
  it('parses the registry, a policy change, servers, discovery, a tool toggle and the OAuth start', async () => {
    nextBody = { builtins: [builtin], remote_servers: [mcpServer] };
    await expect(apiModule.mcpApi.getRegistry()).resolves.toMatchObject({ data: { builtins: [builtin] } });
    nextBody = toolPolicy;
    await expect(apiModule.mcpApi.setToolPolicy('mail', { chat_mode: 'ask' })).resolves.toMatchObject({ data: toolPolicy });
    expect(requests.at(-1)).toEqual({ method: 'PUT', url: '/api/tools/policies/mail' });
    nextBody = [mcpServer];
    await expect(apiModule.mcpApi.listServers()).resolves.toMatchObject({ data: [mcpServer] });
    nextBody = { server: mcpServer, discovered_count: 1 };
    await expect(apiModule.mcpApi.discover(2)).resolves.toMatchObject({ data: { discovered_count: 1 } });
    nextBody = { ...mcpServer.tools[0], enabled: false };
    await expect(apiModule.mcpApi.setToolEnabled(2, 1, false)).resolves.toMatchObject({ data: { enabled: false } });
    nextBody = { authorization_url: 'https://auth.example.com/authorize' };
    await expect(apiModule.mcpApi.startOAuth(2)).resolves.toMatchObject({ data: { authorization_url: expect.any(String) } });
  });

  it('rejects an unknown chat mode, a builtin from another source and a server whose tools are missing', async () => {
    nextBody = { ...toolPolicy, chat_mode: 'never' };
    await expectInvalid(() => apiModule.mcpApi.setToolPolicy('mail', {}), 'PUT', '/api/tools/policies/mail', ['chat_mode']);
    nextBody = { builtins: [{ ...builtin, source: 'remote' }], remote_servers: [] };
    await expectInvalid(() => apiModule.mcpApi.getRegistry(), 'GET', '/api/tools/registry', ['builtins.0.source']);
    nextBody = { ...mcpServer, tools: undefined };
    await expectInvalid(() => apiModule.mcpApi.createServer({ provider_name: 'Docs', endpoint: 'x' }), 'POST', '/api/tools/servers', [
      'tools',
    ]);
  });
});

describe('agent task, delegation and character responses', () => {
  it('parses Agent Tasks pages and details (JAN-204), including a step tool selection (JAN-300)', async () => {
    const { runtimeTaskApi } = await vi.importActual<typeof import('./lib/runtimeTasks')>('./lib/runtimeTasks');
    const fixtures = await import('./components/features/tasks/taskFixtures');
    nextBody = fixtures.taskPage(fixtures.taskSummarySample);
    await expect(runtimeTaskApi.list()).resolves.toMatchObject({ data: { total: fixtures.taskSummarySample.length } });
    for (const detail of [fixtures.completedTaskDetail, fixtures.failedTaskDetail, fixtures.delegatedTaskDetail, fixtures.queuedTaskDetail]) {
      nextBody = detail;
      await expect(runtimeTaskApi.detail({ kind: 'trigger', ref_id: detail.ref_id })).resolves.toMatchObject({ data: { ref_id: detail.ref_id } });
    }
    const step = fixtures.completedTaskDetail.steps[0];
    nextBody = { ...fixtures.completedTaskDetail, steps: [{ ...step, trace: { ...step.trace, tool_selection: 'mail: asked about mail' } }] };
    const detail = await runtimeTaskApi.detail({ kind: 'trigger', ref_id: 'x' });
    expect(detail.data.steps[0].trace?.tool_selection).toBe('mail: asked about mail');
  });

  it('rejects a task with an unknown source and a step without its trace field', async () => {
    const { runtimeTaskApi } = await vi.importActual<typeof import('./lib/runtimeTasks')>('./lib/runtimeTasks');
    const fixtures = await import('./components/features/tasks/taskFixtures');
    nextBody = fixtures.taskPage([{ ...fixtures.taskSummarySample[0], source: 'cron' as never }]);
    await expectInvalid(() => runtimeTaskApi.list(), 'GET', '/api/runtime/tasks/', ['items.0.source']);
    const { trace: _trace, ...stepWithoutTrace } = fixtures.completedTaskDetail.steps[0];
    void _trace;
    nextBody = { ...fixtures.completedTaskDetail, steps: [stepWithoutTrace] };
    await expectInvalid(() => runtimeTaskApi.detail({ kind: 'delegated', ref_id: 'd1' }), 'GET', '/api/runtime/tasks/delegations/d1', [
      'steps.0.trace',
    ]);
  });

  it("parses a conversation's delegated tasks and rejects an unknown delegation status", async () => {
    const { delegatedTasksApi } = await vi.importActual<typeof import('./lib/delegatedTasks')>('./lib/delegatedTasks');
    const delegation = {
      delegation_id: 'd1',
      status: 'running',
      objective: 'Look up the invoice',
      requested_tool_ids: ['mail'],
      allowed_tool_ids: ['mail'],
      result: null,
      error: null,
      depth: 1,
      created_at: '2026-10-01T09:00:00',
      claimed_at: '2026-10-01T09:00:01',
      completed_at: null,
    };
    nextBody = { items: [delegation], total: 1, limit: 20, offset: 0, has_more: false };
    await expect(delegatedTasksApi.list(12)).resolves.toMatchObject({ data: { items: [delegation] } });
    nextBody = { items: [{ ...delegation, status: 'waiting' }], total: 1, limit: 20, offset: 0, has_more: false };
    await expectInvalid(() => delegatedTasksApi.list(12), 'GET', '/api/conversations/12/delegations', ['items.0.status']);
  });

  it('parses the character profile and its preview, and rejects traits without a baseline', async () => {
    nextBody = characterProfile;
    await expect(apiModule.characterApi.get()).resolves.toMatchObject({ data: characterProfile });
    nextBody = { system_prompt: 'You are Janus', memory_instructions: 'Remember facts' };
    await expect(apiModule.characterApi.preview(characterProfile)).resolves.toMatchObject({ data: { system_prompt: 'You are Janus' } });
    nextBody = { ...characterProfile, traits: [{ name: 'warmth' }] };
    await expectInvalid(() => apiModule.characterApi.update(characterProfile), 'PUT', '/api/character', [
      'traits.0.baseline',
      'traits.0.max_delta',
      'traits.0.description',
    ]);
  });

  it('parses the adaptive character state, its history and proposals (JAN-301), and rejects an out-of-set tone', async () => {
    const { characterStateApi } = await vi.importActual<typeof import('./lib/characterState')>('./lib/characterState');
    const fixtures = await import('./components/features/characterState/characterStateFixtures');
    nextBody = fixtures.characterStateView;
    await expect(characterStateApi.get()).resolves.toMatchObject({ data: { revision: fixtures.characterStateView.revision } });
    await expect(characterStateApi.reset()).resolves.toMatchObject({ data: { state: fixtures.characterStateView.state } });
    nextBody = { items: fixtures.characterStateHistory, next_before_id: null };
    await expect(characterStateApi.history()).resolves.toMatchObject({ data: { next_before_id: null } });
    nextBody = fixtures.characterStateProposals;
    await expect(characterStateApi.proposals()).resolves.toMatchObject({ data: fixtures.characterStateProposals });
    nextBody = fixtures.characterStateProposals[0];
    await expect(characterStateApi.reject(fixtures.characterStateProposals[0].id)).resolves.toMatchObject({ data: { id: fixtures.characterStateProposals[0].id } });

    nextBody = { ...fixtures.characterStateView, state: { ...fixtures.characterStateView.state, tone: 'sarcastic' } };
    await expectInvalid(() => characterStateApi.update(fixtures.characterStateView.state), 'PUT', '/api/character/state', ['state.tone']);
  });
});

const whatsappAccount = {
  id: 1,
  display_name: 'Phone',
  provider: 'neonize',
  is_enabled: true,
  connection_status: 'connected',
  initial_history_days: 7,
  last_synced_at: null,
  last_sync_error: null,
  session_database_path: null,
  session_configured: true,
};
const setupSession = {
  id: 3,
  account_id: null,
  display_name: 'Phone',
  initial_history_days: 7,
  setup_status: 'pairing',
  pairing_state: 'code_ready',
  pairing_supported: true,
  show_fallback_import: false,
  pairing_message: 'Scan the code',
  pairing_code: 'ABCD-1234',
  pairing_qr_payload: 'qr',
  last_error: null,
  expires_at: '2026-10-01T09:10:00',
  completed_at: null,
  session_configured: false,
};
const whatsappMessage = {
  id: 9,
  whatsapp_account_id: 1,
  whatsapp_conversation_id: 2,
  provider_message_id: 'm1',
  provider_timestamp: '2026-10-01T09:00:00',
  direction: 'incoming',
  sender_display_name: 'Ada',
  body: 'Hi',
  snippet: 'Hi',
  priority: 'important',
};
const stats = {
  days: 7,
  timezone: 'UTC',
  start: '2026-10-06',
  end: '2026-10-12',
  dates: ['2026-10-12'],
  mail: {
    per_day: [{ date: '2026-10-12', important: 1, digest: 0, ignore: 0, spam: 0 }],
    totals: { important: 1, digest: 0, ignore: 0, spam: 0 },
    total: 1,
  },
  triggers: {
    per_day: [{ date: '2026-10-12', completed: 1, failed: 0, cancelled: 0, pending: 0 }],
    totals: { completed: 1, failed: 0, cancelled: 0, pending: 0 },
    total: 1,
    success_rate: 1,
    definitions: [
      {
        definition_id: null,
        name: null,
        origin: null,
        runs: 1,
        completed: 1,
        failed: 0,
        cancelled: 0,
        pending: 0,
        success_rate: 1,
        avg_duration_seconds: 2.5,
        last_run_at: '2026-10-12T09:00:00',
      },
    ],
  },
  memory: {
    per_day: [{ date: '2026-10-12', new_facts: 0 }],
    new_facts: 0,
    active_facts: 3,
    archived_facts: 0,
    active_topics: 1,
    total_topics: 1,
    wiki_pages: 0,
    knowledge_sources: 2,
  },
  messaging: {
    per_day: [{ date: '2026-10-12', whatsapp_in: 1, whatsapp_out: 0, chat_user: 2, chat_assistant: 2 }],
    totals: { whatsapp_in: 1, whatsapp_out: 0, chat_user: 2, chat_assistant: 2 },
  },
  approvals: {
    automations: {
      totals: { proposed: 1, approved: 4, rejected: 1, withdrawn: 0, superseded: 0 },
      total: 6,
      approval_rate: 0.8,
      median_hours_to_decision: 3.5,
    },
    chat_tools: {
      totals: { approved: 9, denied: 1, timeout: 2, disconnected: 0, unavailable: 0 },
      total: 12,
      approval_rate: 0.9,
      median_hours_to_decision: null,
    },
    character: { totals: { proposed: 0, approved: 1, rejected: 1 }, total: 2, approval_rate: 0.5, median_hours_to_decision: 12 },
  },
  llm: {
    per_day: [],
    by_surface: [{ surface: 'chat', calls: 4, input_tokens: 1200, output_tokens: 300 }],
    by_model: [{ model: 'ollama/llama3', calls: 4, input_tokens: 1200, output_tokens: 300 }],
    totals: { calls: 4, input_tokens: 1200, output_tokens: 300, unreported_calls: 0 },
  },
};

describe('WhatsApp responses', () => {
  it('parses accounts, setup sessions, completion, the connector restart, conversations and messages', async () => {
    nextBody = [whatsappAccount];
    await expect(apiModule.whatsappApi.getAccounts()).resolves.toMatchObject({ data: [whatsappAccount] });
    nextBody = setupSession;
    await expect(apiModule.whatsappApi.startPairing(3)).resolves.toMatchObject({ data: { pairing_code: 'ABCD-1234' } });
    const syncResult = { account_id: 1, connector_started: true, message: 'Started' };
    nextBody = { account: whatsappAccount, sync_result: syncResult };
    await expect(apiModule.whatsappApi.completeSetupSession(3)).resolves.toMatchObject({ data: { sync_result: syncResult } });
    nextBody = syncResult;
    await expect(apiModule.whatsappApi.restartConnector(1)).resolves.toMatchObject({ data: syncResult });
    nextBody = { status: 'success', message: 'Ready' };
    await expect(apiModule.whatsappApi.testAccount(1)).resolves.toMatchObject({ data: { status: 'success' } });
    nextBody = [{ id: 2, whatsapp_account_id: 1, provider_chat_id: 'c@s', title: 'Ada', conversation_type: 'direct', unread_count: 0 }];
    await expect(apiModule.whatsappApi.getConversations()).resolves.toMatchObject({ data: [{ title: 'Ada' }] });
    nextBody = [whatsappMessage];
    await expect(apiModule.whatsappApi.getMessages()).resolves.toMatchObject({ data: [whatsappMessage] });
    await expect(apiModule.dashboardApi.getWhatsAppMessages()).resolves.toMatchObject({ data: [whatsappMessage] });
  });

  it('rejects an unknown pairing state, a message without its direction and a completion without its account', async () => {
    nextBody = { ...setupSession, pairing_state: 'linked' };
    await expectInvalid(() => apiModule.whatsappApi.getSetupSession(3), 'GET', '/api/whatsapp/setup-sessions/3', ['pairing_state']);
    nextBody = { ...whatsappMessage, direction: undefined };
    await expectInvalid(() => apiModule.whatsappApi.updatePriority(9, 'digest'), 'PATCH', '/api/whatsapp/messages/9/priority', [
      'direction',
    ]);
    nextBody = { sync_result: { account_id: 1, connector_started: true, message: '' } };
    await expectInvalid(() => apiModule.whatsappApi.completeSetupSession(3), 'POST', '/api/whatsapp/setup-sessions/3/complete', [
      'account',
    ]);
  });
});

describe('Google calendar, system event, error group and stats responses', () => {
  it('parses calendars and the auth URL, and rejects a non-http auth URL and a calendar without its selection', async () => {
    const calendar = { id: 'primary', summary: 'Ada', primary: true, access_role: 'owner', selected: true };
    nextBody = [calendar];
    await expect(apiModule.connectionsApi.getCalendars('google')).resolves.toMatchObject({ data: [calendar] });
    await expect(apiModule.connectionsApi.updateCalendars('google', ['primary'])).resolves.toMatchObject({ data: [calendar] });
    nextBody = { url: 'https://accounts.google.com/o/oauth2/auth?state=x' };
    await expect(apiModule.connectionsApi.getAuthUrl('google')).resolves.toMatchObject({ data: { url: expect.any(String) } });

    nextBody = { url: 'javascript:alert(1)' };
    await expectInvalid(() => apiModule.connectionsApi.getAuthUrl('google'), 'GET', '/api/connections/google/auth-url', ['url']);
    nextBody = [{ ...calendar, selected: undefined }];
    await expectInvalid(() => apiModule.connectionsApi.getCalendars('google'), 'GET', '/api/planner/google/calendars', ['0.selected']);
  });

  it('parses the system event log and its sources, and rejects an unknown event type', async () => {
    const event = { id: 1, timestamp: '2026-10-01T09:00:00', source: 'imap', message: 'Synced', event_type: 'SUCCESS', details: null };
    nextBody = { items: [event], total: 1, limit: 50, offset: 0, has_more: false };
    await expect(apiModule.dashboardApi.getEvents({ limit: 50 })).resolves.toMatchObject({ data: { items: [event] } });
    nextBody = ['imap', 'planner'];
    await expect(apiModule.dashboardApi.getEventSources()).resolves.toMatchObject({ data: ['imap', 'planner'] });

    nextBody = { items: [{ ...event, event_type: 'DEBUG' }], total: 1, limit: 50, offset: 0, has_more: false };
    await expectInvalid(() => apiModule.dashboardApi.getEvents(), 'GET', '/api/events', ['items.0.event_type']);
    nextBody = [1];
    await expectInvalid(() => apiModule.dashboardApi.getEventSources(), 'GET', '/api/events/sources', ['0']);
  });

  it('parses error groups and a compaction, and rejects a group without its count', async () => {
    const group = {
      id: 1,
      fingerprint: 'abc',
      source: 'imap',
      message_template: 'Login failed for <n>',
      first_seen: '2026-10-01T09:00:00',
      last_seen: '2026-10-02T09:00:00',
      occurrence_count: 3,
      sample_details: null,
      last_compacted_at: null,
      created_at: '2026-10-01T09:00:00',
    };
    nextBody = [group];
    await expect(apiModule.errorApi.listGroups()).resolves.toMatchObject({ data: [group] });
    nextBody = { events_processed: 3, groups_updated: 1, events_deleted: 2, groups_deleted: 0 };
    await expect(apiModule.errorApi.compact()).resolves.toMatchObject({ data: { events_deleted: 2 } });

    nextBody = { ...group, occurrence_count: '3' };
    await expectInvalid(() => apiModule.errorApi.getGroup(1), 'GET', '/api/errors/groups/1', ['occurrence_count']);
  });

  it('parses the stats summary and rejects an unknown range and a totals map missing a priority', async () => {
    nextBody = stats;
    await expect(apiModule.statsApi.get(7, 'UTC')).resolves.toMatchObject({ data: stats });

    nextBody = { ...stats, days: 14, mail: { ...stats.mail, totals: { important: 1, digest: 0, ignore: 0 } } };
    await expectInvalid(() => apiModule.statsApi.get(7), 'GET', '/api/stats', ['days', 'mail.totals.spam']);
  });
});
