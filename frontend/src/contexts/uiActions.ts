import { createContext, useContext } from 'react';

import type { PageContextItem, PageContextSnapshot } from '../lib/pageContext';
import type { ToolCallRecord } from '../types';

/**
 * Page actions (JAN-251): Janus asks the page the user has open to navigate,
 * set a filter, open/highlight an item, scroll to a section, or prefill a form.
 * The backend validates each request against its allowlists and the message's
 * page snapshot and sends a `__UI_ACTION__` frame; this module checks it again
 * against what the *current* page registered and publishes, runs it, and
 * reports `{ok, error}`. Nothing here can submit a form or touch server data,
 * and there is no selector access: elements are found only through the fixed
 * `data-ui-item` / `data-ui-section` attributes.
 */

/** Prefix of the `/ws/chat` page-action frame (JAN-251). */
export const UI_ACTION_PREFIX = '__UI_ACTION__:';

export const UI_ACTION_NAMES = ['navigate', 'set_filter', 'open_item', 'highlight', 'scroll_to', 'prefill_form'] as const;
export type UIActionName = (typeof UI_ACTION_NAMES)[number];

export interface UIActionRequest {
  action_id: string;
  call_id: string;
  action: string;
  params: Record<string, unknown>;
  label: string;
}

/** Parses a `__UI_ACTION__:` frame body; `null` for malformed payloads. */
export const parseUIAction = (body: string): UIActionRequest | null => {
  try {
    const value = JSON.parse(body) as Record<string, unknown> | null;
    if (!value || typeof value !== 'object') return null;
    if (typeof value.action_id !== 'string' || !value.action_id || typeof value.action !== 'string') return null;
    const params = value.params && typeof value.params === 'object' ? (value.params as Record<string, unknown>) : {};
    return {
      action_id: value.action_id,
      call_id: typeof value.call_id === 'string' ? value.call_id : '',
      action: value.action,
      params,
      label: typeof value.label === 'string' ? value.label : '',
    };
  } catch {
    return null;
  }
};

const MEMORY_TABS = ['facts', 'topics', 'documents', 'knowledge', 'wiki', 'rules'];
const SETTINGS_TABS = [
  'system', 'connections', 'tools', 'character', 'skills', 'runtime', 'briefing', 'security', 'appearance',
  'status', 'stats', 'events',
  // Admin-only in the UI; Settings.tsx redirects non-admins away from it.
  'errors',
];
/** Navigation targets, mirroring the backend's `ROUTES` (`services/ui_actions.py`): path -> allowed `tab` values. */
const ROUTE_TABS: Record<string, readonly string[]> = {
  '/': [],
  '/chat': [],
  '/briefing': [],
  '/emails': [],
  '/planner': [],
  '/docs': [],
  '/memory': MEMORY_TABS,
  '/settings': SETTINGS_TABS,
};

/** Returns the allowlisted route (`/path` or `/path?tab=x`), or `null` for anything else. */
export const normalizeRoute = (route: unknown): string | null => {
  if (typeof route !== 'string' || !route.startsWith('/') || route.startsWith('//') || route.includes('#')) return null;
  const [rawPath, query = ''] = route.split('?', 2);
  const path = rawPath.replace(/\/+$/, '') || '/';
  const tabs = ROUTE_TABS[path];
  if (!tabs) return null;
  if (!query) return path;
  const entries = [...new URLSearchParams(query).entries()];
  if (entries.length !== 1 || entries[0][0] !== 'tab' || !tabs.includes(entries[0][1])) return null;
  return `${path}?tab=${entries[0][1]}`;
};

const MAX_FREE_TEXT = 80;
const MAX_FORM_VALUE = 1000;

/** A filter a page lets Janus set: its allowed values (none = short free text), a getter and a setter. */
export interface PageFilterAction {
  values?: readonly string[];
  get: () => string;
  set: (value: string) => void;
}

/** What a page can do with one item type: open it (returning a close function for undo) and highlight it. */
export interface PageItemAction {
  open?: (id: string | number) => (() => void) | void;
  highlight?: boolean;
}

/** A form Janus may fill in (never submit): its field names, the current values, and a setter. */
export interface PageFormAction {
  fields: readonly string[];
  get: () => Record<string, string>;
  prefill: (values: Record<string, string>) => void;
}

/** Everything one page (or section of a page) supports, registered while it is mounted. */
export interface PageActions {
  /** Page id used by `set_filter` (`emails`, `topics`, `triggers`, `dashboard`, `settings`). */
  page: string;
  /** The route path the page lives on; a `tab` filter here follows `navigate` to `path?tab=...`. */
  path?: string;
  filters?: Record<string, PageFilterAction>;
  items?: Record<string, PageItemAction>;
  sections?: readonly string[];
  forms?: Record<string, PageFormAction>;
}

/** What the store needs from the app: the router, the page snapshot, and the two DOM lookups. */
export interface UIActionEnv {
  navigate: (route: string) => void;
  currentRoute: () => string;
  snapshot: () => PageContextSnapshot;
  highlight: (type: string, id: string | number) => boolean;
  scrollTo: (section: string) => boolean;
  /** How long a request that follows a navigation waits for the new page (ms). */
  waitMs?: number;
}

export interface UIActionOutcome {
  ok: boolean;
  error?: string;
  /** The target as the transcript chip names it. */
  label: string;
  /** Cheap client-side undo, when there is one. */
  undo?: () => void;
}

/** Not possible now, but maybe once the page that was just navigated to has rendered. */
class NotYet extends Error {}
/** Never possible: an unknown action, route, field, or value. */
class Rejected extends Error {}

const DEFAULT_WAIT_MS = 3000;
const POLL_MS = 100;

const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

const findItem = (snapshot: PageContextSnapshot, type: unknown, id: unknown): PageContextItem | null => {
  if (typeof type !== 'string' || (typeof id !== 'string' && typeof id !== 'number')) return null;
  const items = [...(snapshot.selection ? [snapshot.selection] : []), ...(snapshot.visible_items ?? [])];
  return items.find((item) => item.type === type && String(item.id) === String(id)) ?? null;
};

/** The current registrations, newest last. */
type Registrations = () => PageActions[];
type Handler = (params: Record<string, unknown>, env: UIActionEnv, all: Registrations) => Omit<UIActionOutcome, 'ok'>;

const filterOf = (all: Registrations, page: string, key: string) =>
  all().find((actions) => actions.page === page && actions.filters?.[key])?.filters?.[key];
const formOf = (all: Registrations, formId: string) => all().find((actions) => actions.forms?.[formId])?.forms?.[formId];

/** Navigates, then lets a page that is already open on that path follow the route's `tab`. */
const goTo = (all: Registrations, route: string, env: UIActionEnv) => {
  env.navigate(route);
  const [path, query = ''] = route.split('?', 2);
  const tab = new URLSearchParams(query).get('tab');
  const tabFilter = all().find((actions) => actions.path === path && actions.filters?.tab)?.filters?.tab;
  if (tab && tabFilter && tabFilter.get() !== tab) tabFilter.set(tab);
};

const itemOnPage = (params: Record<string, unknown>, env: UIActionEnv) => {
  const found = findItem(env.snapshot(), params.type, params.id);
  if (!found) throw new NotYet(`${String(params.type)} ${String(params.id)} is not on the page`);
  return found;
};

const checkFilterValue = (filter: PageFilterAction, key: string, value: unknown): string => {
  if (typeof value !== 'string') throw new Rejected(`${key} needs a text value`);
  const allowed = filter.values ? filter.values.includes(value) : value.length <= MAX_FREE_TEXT;
  if (!allowed) throw new Rejected(`${key} cannot be set to ${value}`);
  return value;
};

const checkFormFields = (form: PageFormAction, formId: string, raw: unknown): Record<string, string> => {
  const fields = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  if (Object.keys(fields).length === 0) throw new Rejected('no fields to fill in');
  for (const [name, value] of Object.entries(fields)) {
    if (!form.fields.includes(name)) throw new Rejected(`form ${formId} has no field ${name}`);
    if (typeof value !== 'string' || value.length > MAX_FORM_VALUE) throw new Rejected(`${name} needs short text`);
  }
  return fields as Record<string, string>;
};

const HANDLER_TABLE: Record<UIActionName, Handler> = {
  navigate: (params, env, all) => {
    const route = normalizeRoute(params.route);
    if (!route) throw new Rejected(`unknown route ${String(params.route)}`);
    const previous = env.currentRoute();
    goTo(all, route, env);
    return { label: route, undo: () => goTo(all, previous, env) };
  },
  set_filter: (params, _env, all) => {
    const page = String(params.page);
    const key = String(params.key);
    const filter = filterOf(all, page, key);
    if (!filter) throw new NotYet(`the ${page} page with a ${key} filter is not open`);
    const value = checkFilterValue(filter, key, params.value);
    const previous = filter.get();
    filter.set(value);
    return { label: `${key} = ${value || '(empty)'}`, undo: () => filterOf(all, page, key)?.set(previous) };
  },
  open_item: (params, env, all) => {
    const found = itemOnPage(params, env);
    const support = all().find((actions) => actions.items?.[found.type])?.items?.[found.type];
    if (!support?.open && !support?.highlight) throw new NotYet(`this page cannot open ${found.type} items`);
    // An item without a detail view (a calendar event) is "opened" by scrolling to it.
    if (!support.open && !env.highlight(found.type, found.id)) throw new NotYet(`${found.label} is not shown`);
    return { label: found.label, undo: support.open?.(found.id) ?? undefined };
  },
  highlight: (params, env, all) => {
    const found = itemOnPage(params, env);
    if (!all().some((actions) => actions.items?.[found.type]?.highlight)) {
      throw new NotYet(`this page cannot highlight ${found.type} items`);
    }
    if (!env.highlight(found.type, found.id)) throw new NotYet(`${found.label} is not shown on the page`);
    return { label: found.label };
  },
  scroll_to: (params, env, all) => {
    const section = String(params.section);
    if (!all().some((actions) => actions.sections?.includes(section))) {
      throw new NotYet(`this page has no section ${section}`);
    }
    if (!env.scrollTo(section)) throw new NotYet(`section ${section} is not shown on the page`);
    return { label: section };
  },
  prefill_form: (params, _env, all) => {
    const formId = String(params.form_id);
    const form = formOf(all, formId);
    if (!form) throw new NotYet(`form ${formId} is not on the page`);
    const fields = checkFormFields(form, formId, params.fields);
    const current = form.get();
    const previous = Object.fromEntries(Object.keys(fields).map((name) => [name, current[name] ?? '']));
    form.prefill(fields);
    return { label: formId, undo: () => formOf(all, formId)?.prefill(previous) };
  },
};

/** Looked up with `get`, so an action name from the wire can only ever reach one of these handlers. */
const HANDLERS = new Map<string, Handler>(Object.entries(HANDLER_TABLE));

export interface UIActionStore {
  register: (owner: symbol, actions: PageActions) => void;
  clear: (owner: symbol) => void;
  /** Validates and runs one request against the current page; never throws. */
  execute: (request: UIActionRequest, env: UIActionEnv) => Promise<UIActionOutcome>;
}

export const createUIActionStore = (): UIActionStore => {
  const registrations = new Map<symbol, PageActions>();
  const all: Registrations = () => [...registrations.values()];

  const attempt = (request: UIActionRequest, env: UIActionEnv): Omit<UIActionOutcome, 'ok'> => {
    const handler = HANDLERS.get(request.action);
    if (!handler) throw new Rejected(`unknown page action ${request.action}`);
    return handler(request.params, env, all);
  };

  return {
    register(owner, actions) {
      registrations.set(owner, actions);
    },
    clear(owner) {
      registrations.delete(owner);
    },
    async execute(request, env) {
      // A request that follows a navigation in the same reply waits for the new page to register.
      const deadline = Date.now() + (env.waitMs ?? DEFAULT_WAIT_MS);
      for (;;) {
        try {
          return { ok: true, ...attempt(request, env) };
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          if (!(error instanceof NotYet) || Date.now() >= deadline) {
            return { ok: false, error: message, label: request.label };
          }
        }
        await sleep(POLL_MS);
      }
    },
  };
};

/** The tab's page-action runtime: the registry plus the function that runs one frame's request. */
export interface UIActionRuntime {
  store: UIActionStore;
  perform: (request: UIActionRequest) => Promise<UIActionOutcome>;
}

/** One executed (or failed) page action, for its transcript chip. */
export interface ExecutedUIAction {
  action: string;
  label: string;
  ok: boolean;
  undo?: () => void;
  undone: boolean;
}

export interface UIActionLog {
  executed: Record<string, ExecutedUIAction>;
  undo: (callId: string) => void;
}

export const UIActionRuntimeContext = createContext<UIActionRuntime | null>(null);
export const UIActionLogContext = createContext<UIActionLog | null>(null);

/** The executed page actions by `call_id`, or `null` outside a `UIActionProvider` (stories, tests). */
export const useUIActionLog = (): UIActionLog | null => useContext(UIActionLogContext);

const UI_ACTION_TOOL_PREFIX = 'ui_';

/** A stored call's target from its (redacted) arguments, for chips without a live action (after a reload). */
const STORED_TARGETS = new Map<string, (args: Record<string, unknown>) => string>([
  ['navigate', (args) => String(args.route ?? '')],
  ['set_filter', (args) => `${String(args.key)} = ${String(args.value)}`],
  ['open_item', (args) => `${String(args.type)} ${String(args.id)}`],
  ['highlight', (args) => `${String(args.type)} ${String(args.id)}`],
  ['scroll_to', (args) => String(args.section ?? '')],
  ['prefill_form', (args) => String(args.form_id ?? '')],
]);

/** How a transcript chip names a page-action call (`open_item · Invoice overdue`); `null` for any other tool. */
export const describeUIActionCall = (call: ToolCallRecord, executed?: ExecutedUIAction): string | null => {
  if (executed) return `${executed.action} · ${executed.label}`;
  const verb = call.tool_name.startsWith(UI_ACTION_TOOL_PREFIX) ? call.tool_name.slice(UI_ACTION_TOOL_PREFIX.length) : '';
  const target = STORED_TARGETS.get(verb);
  if (call.tool_id !== 'ui_actions' || !target) return null;
  const args = call.args && typeof call.args === 'object' ? (call.args as Record<string, unknown>) : {};
  return `${verb} · ${target(args)}`;
};
