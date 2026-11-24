import type { QueryClient, QueryKey } from '@tanstack/react-query';

/**
 * Live UI cache invalidation from backend domain changes (JAN-104).
 *
 * The backend publishes the *names* of the domains a committed write touched
 * (`src/janus/domain_changes.py`) over `/ws/changes`; this module maps each
 * domain to the TanStack Query key prefixes that read it. Every key here is a
 * prefix, so `['dashboard', 'tasks']` also refreshes `['dashboard', 'tasks', …]`.
 *
 * Adding a domain: add it to `DOMAINS` in `domain_changes.py` and here — a
 * backend test fails while the two lists differ.
 */
export const DOMAIN_QUERY_KEYS = {
  memory: [['memory-facts'], ['memory-compaction-latest']],
  topics: [['topics'], ['topic-detail'], ['topic-suggestions'], ['dashboard', 'topics']],
  rules: [['email-rules']],
  email_clarifications: [['email-clarifications']],
  emails: [['emails-list'], ['dashboard', 'important-emails']],
  planner: [['dashboard', 'tasks'], ['dashboard', 'schedule'], ['planner']],
  skills: [['skills']],
  dashboard_widgets: [['dashboard', 'widgets']],
  // Janus's suggested automations are trigger_definitions rows too (JAN-235): list, history, and the nav-badge count.
  trigger_definitions: [['trigger-definitions'], ['trigger-proposals']],
  trigger_runs: [['runtime-triggers'], ['trigger-definitions']],
  knowledge: [
    ['knowledge-sources'],
    ['knowledge-entities'],
    ['knowledge-entity'],
    ['knowledge-merge-suggestions'],
    ['knowledge-extraction'],
  ],
  wiki: [['wiki-pages'], ['wiki-page']],
  // The adaptive state, its history and Janus's proposals (JAN-301) too.
  character: [['character-profile'], ['character-state'], ['character-state-history'], ['character-state-proposals']],
  // A proposal's blocked tools follow the unattended switch, so a policy change refreshes the review cards.
  tools: [['mcp-registry'], ['trigger-proposals']],
  // Account rows too: the connector thread flips their connection status (JAN-223).
  whatsapp: [['dashboard', 'whatsapp-messages'], ['whatsapp-accounts']],
  // A conversation Janus started (JAN-319), or one opened elsewhere: the history list and the Chat badge.
  conversations: [['conversations']],
} satisfies Record<string, QueryKey[]>;

type DomainName = keyof typeof DOMAIN_QUERY_KEYS;

const ALL_DOMAINS = Object.keys(DOMAIN_QUERY_KEYS) as DomainName[];

const isDomainName = (value: unknown): value is DomainName =>
  typeof value === 'string' && Object.prototype.hasOwnProperty.call(DOMAIN_QUERY_KEYS, value);

/** The de-duplicated query key prefixes to invalidate for a set of domains; unknown names are ignored. */
export const queryKeysForDomains = (domains: Iterable<string>): QueryKey[] => {
  const seen = new Set<string>();
  const keys: QueryKey[] = [];
  for (const domain of domains) {
    if (!isDomainName(domain)) continue;
    for (const key of DOMAIN_QUERY_KEYS[domain]) {
      const id = JSON.stringify(key);
      if (seen.has(id)) continue;
      seen.add(id);
      keys.push(key);
    }
  }
  return keys;
};

export type DomainChangeFrame = { type: 'ready' } | { type: 'changed'; domains: string[] };

/** Parse one `/ws/changes` frame; heartbeats and malformed frames return `null`. */
export const parseDomainChangeFrame = (data: unknown): DomainChangeFrame | null => {
  if (typeof data !== 'string') return null;
  let frame: { type?: unknown; domains?: unknown };
  try {
    frame = JSON.parse(data) as { type?: unknown; domains?: unknown };
  } catch {
    return null;
  }
  if (frame?.type === 'ready') return { type: 'ready' };
  if (frame?.type === 'changed' && Array.isArray(frame.domains)) {
    return { type: 'changed', domains: frame.domains.filter((domain): domain is string => typeof domain === 'string') };
  }
  return null;
};

interface DomainChangeSyncOptions {
  queryClient: QueryClient;
  url: string;
  /** Collect bursts of changes for this long before invalidating. */
  debounceMs?: number;
  /** Reconnect delays grow from this to `maxReconnectMs`. */
  initialReconnectMs?: number;
  maxReconnectMs?: number;
  createSocket?: (url: string) => WebSocket;
}

/**
 * Open the tab's single `/ws/changes` socket and invalidate the mapped queries
 * for every reported domain. Every `ready` frame, the first one included,
 * invalidates every mapped domain once: a commit made after a query loaded but
 * before the subscription existed (or while the socket was down) produced no
 * frame. Only active queries refetch, so this costs one round of the open
 * view's requests per connect. Returns a function that stops syncing.
 */
export const startDomainChangeSync = ({
  queryClient,
  url,
  debounceMs = 150,
  initialReconnectMs = 1000,
  maxReconnectMs = 30000,
  createSocket = (socketUrl) => new WebSocket(socketUrl),
}: DomainChangeSyncOptions): (() => void) => {
  let socket: WebSocket | null = null;
  let stopped = false;
  let reconnectDelay = initialReconnectMs;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  const pending = new Set<string>();

  const flush = () => {
    flushTimer = null;
    const keys = queryKeysForDomains(pending);
    pending.clear();
    for (const queryKey of keys) {
      void queryClient.invalidateQueries({ queryKey });
    }
  };

  const enqueue = (domains: Iterable<string>) => {
    for (const domain of domains) pending.add(domain);
    if (pending.size > 0 && flushTimer == null) {
      flushTimer = setTimeout(flush, debounceMs);
    }
  };

  const scheduleReconnect = () => {
    if (stopped || reconnectTimer != null) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, maxReconnectMs);
  };

  const connect = () => {
    if (stopped) return;
    const current = createSocket(url);
    socket = current;
    current.onmessage = (event: MessageEvent) => {
      if (socket !== current) return;
      const frame = parseDomainChangeFrame(event.data);
      if (frame?.type === 'ready') {
        reconnectDelay = initialReconnectMs;
        enqueue(ALL_DOMAINS);
      } else if (frame?.type === 'changed') {
        enqueue(frame.domains);
      }
    };
    current.onclose = () => {
      if (socket !== current) return;
      socket = null;
      scheduleReconnect();
    };
  };

  connect();

  return () => {
    stopped = true;
    if (reconnectTimer != null) clearTimeout(reconnectTimer);
    if (flushTimer != null) clearTimeout(flushTimer);
    const current = socket;
    socket = null;
    current?.close();
  };
};
