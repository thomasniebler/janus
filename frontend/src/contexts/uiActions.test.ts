import { describe, expect, it, vi } from 'vitest';

import type { PageContextSnapshot } from '../lib/pageContext';
import type { ToolCallRecord } from '../types';
import {
  createUIActionStore,
  describeUIActionCall,
  normalizeRoute,
  parseUIAction,
  type PageActions,
  type UIActionEnv,
  type UIActionRequest,
} from './uiActions';

const SNAPSHOT: PageContextSnapshot = {
  page: 'Emails',
  route: '/emails',
  selection: { type: 'email', id: 11, label: 'Invoice overdue — from billing@example.com' },
  visible_items: [
    { type: 'email', id: 11, label: 'Invoice overdue — from billing@example.com' },
    { type: 'email', id: 12, label: 'Lunch? — from anna@example.com' },
  ],
};

const request = (action: string, params: Record<string, unknown>): UIActionRequest => ({
  action_id: `a-${action}`,
  call_id: `c-${action}`,
  action,
  params,
  label: 'server label',
});

/** An env whose router just records routes; nothing waits unless a test asks. */
const makeEnv = (overrides: Partial<UIActionEnv> = {}) => {
  let route = '/emails';
  const env: UIActionEnv = {
    navigate: vi.fn((to: string) => {
      route = to;
    }),
    currentRoute: () => route,
    snapshot: () => SNAPSHOT,
    highlight: vi.fn(() => true),
    scrollTo: vi.fn(() => true),
    waitMs: 0,
    ...overrides,
  };
  return env;
};

const emailsPage = () => {
  const state = { priority: 'all', open: null as number | null, form: { title: '', summary: '' } };
  const actions: PageActions = {
    page: 'emails',
    path: '/emails',
    filters: {
      priority: { values: ['all', 'important'], get: () => state.priority, set: (value) => (state.priority = value) },
      search: { get: () => '', set: vi.fn() },
    },
    items: {
      email: {
        open: (id) => {
          const previous = state.open;
          state.open = Number(id);
          return () => (state.open = previous);
        },
        highlight: true,
      },
    },
    sections: ['important_emails'],
    forms: {
      'topic.create': {
        fields: ['title', 'summary'],
        get: () => ({ ...state.form }),
        prefill: (values) => Object.assign(state.form, values),
      },
    },
  };
  return { state, actions };
};

describe('parseUIAction', () => {
  it('reads a frame and rejects malformed ones', () => {
    expect(
      parseUIAction(JSON.stringify({ action_id: 'a', call_id: 'c', action: 'navigate', params: { route: '/' }, label: '/' }))
    ).toEqual({ action_id: 'a', call_id: 'c', action: 'navigate', params: { route: '/' }, label: '/' });
    expect(parseUIAction('not json')).toBeNull();
    expect(parseUIAction(JSON.stringify({ action: 'navigate' }))).toBeNull();
    expect(parseUIAction(JSON.stringify({ action_id: 'a', action: 'navigate', params: 'x' }))?.params).toEqual({});
  });
});

describe('normalizeRoute', () => {
  it('accepts only the app routes and their tabs', () => {
    expect(normalizeRoute('/')).toBe('/');
    expect(normalizeRoute('/emails/')).toBe('/emails');
    expect(normalizeRoute('/settings?tab=runtime')).toBe('/settings?tab=runtime');
    expect(normalizeRoute('/memory?tab=topics')).toBe('/memory?tab=topics');
    // Admin-only tab: allowed here, Settings.tsx still redirects non-admins.
    expect(normalizeRoute('/settings?tab=errors')).toBe('/settings?tab=errors');
    for (const bad of [
      'https://evil.example.com/',
      '//evil.example.com',
      '/admin',
      '/emails?email=3',
      '/settings?tab=nope',
      '/settings?tab=runtime&x=1',
      '/memory#top',
      'javascript:alert(1)',
      42,
    ]) {
      expect(normalizeRoute(bad)).toBeNull();
    }
  });
});

describe('UIActionStore', () => {
  it('navigates to an allowlisted route and undoes back to the previous one', async () => {
    const store = createUIActionStore();
    const env = makeEnv();
    const outcome = await store.execute(request('navigate', { route: '/settings?tab=runtime' }), env);
    expect(outcome).toMatchObject({ ok: true, label: '/settings?tab=runtime' });
    expect(env.navigate).toHaveBeenLastCalledWith('/settings?tab=runtime');
    outcome.undo?.();
    expect(env.navigate).toHaveBeenLastCalledWith('/emails');
  });

  it('switches the tab of a page that is already open on the target path', async () => {
    const store = createUIActionStore();
    let tab = 'accounts';
    store.register(Symbol('settings'), {
      page: 'settings',
      path: '/settings',
      filters: { tab: { values: ['accounts', 'runtime'], get: () => tab, set: (value) => (tab = value) } },
    });
    const env = makeEnv({ currentRoute: () => '/settings?tab=accounts' });
    const outcome = await store.execute(request('navigate', { route: '/settings?tab=runtime' }), env);
    expect(tab).toBe('runtime');
    outcome.undo?.();
    expect(tab).toBe('accounts');
  });

  it('rejects unknown routes and unknown actions without navigating', async () => {
    const store = createUIActionStore();
    const env = makeEnv();
    expect(await store.execute(request('navigate', { route: 'https://evil.example.com' }), env)).toMatchObject({
      ok: false,
      error: 'unknown route https://evil.example.com',
    });
    expect(await store.execute(request('submit_form', {}), env)).toMatchObject({
      ok: false,
      error: 'unknown page action submit_form',
    });
    expect(env.navigate).not.toHaveBeenCalled();
  });

  it('sets a registered filter to an allowed value and restores the previous value on undo', async () => {
    const store = createUIActionStore();
    const { state, actions } = emailsPage();
    store.register(Symbol('emails'), actions);
    const outcome = await store.execute(request('set_filter', { page: 'emails', key: 'priority', value: 'important' }), makeEnv());
    expect(outcome).toMatchObject({ ok: true, label: 'priority = important' });
    expect(state.priority).toBe('important');
    outcome.undo?.();
    expect(state.priority).toBe('all');
  });

  it('rejects filters the page did not register and values it does not allow', async () => {
    const store = createUIActionStore();
    const { state, actions } = emailsPage();
    store.register(Symbol('emails'), actions);
    const env = makeEnv();
    expect(await store.execute(request('set_filter', { page: 'emails', key: 'priority', value: 'spam' }), env)).toMatchObject({
      ok: false,
      error: 'priority cannot be set to spam',
    });
    expect(await store.execute(request('set_filter', { page: 'topics', key: 'status', value: 'all' }), env)).toMatchObject({
      ok: false,
      error: 'the topics page with a status filter is not open',
    });
    expect(
      await store.execute(request('set_filter', { page: 'emails', key: 'search', value: 'x'.repeat(81) }), env)
    ).toMatchObject({ ok: false });
    expect(state.priority).toBe('all');
  });

  it('opens only items in the current snapshot and closes them again on undo', async () => {
    const store = createUIActionStore();
    const { state, actions } = emailsPage();
    store.register(Symbol('emails'), actions);
    const env = makeEnv();
    const opened = await store.execute(request('open_item', { type: 'email', id: '12' }), env);
    expect(opened).toMatchObject({ ok: true, label: 'Lunch? — from anna@example.com' });
    expect(state.open).toBe(12);
    opened.undo?.();
    expect(state.open).toBeNull();

    expect(await store.execute(request('open_item', { type: 'email', id: 99 }), env)).toMatchObject({
      ok: false,
      error: 'email 99 is not on the page',
    });
    expect(await store.execute(request('open_item', { type: 'topic', id: 11 }), env)).toMatchObject({ ok: false });
    expect(state.open).toBeNull();
  });

  it('highlights and scrolls only to registered item types and sections', async () => {
    const store = createUIActionStore();
    const env = makeEnv();
    expect(await store.execute(request('highlight', { type: 'email', id: 11 }), env)).toMatchObject({
      ok: false,
      error: 'this page cannot highlight email items',
    });
    store.register(Symbol('emails'), emailsPage().actions);
    expect(await store.execute(request('highlight', { type: 'email', id: 11 }), env)).toMatchObject({
      ok: true,
      label: 'Invoice overdue — from billing@example.com',
    });
    expect(env.highlight).toHaveBeenCalledWith('email', 11);
    expect(await store.execute(request('scroll_to', { section: 'important_emails' }), env)).toMatchObject({ ok: true });
    expect(await store.execute(request('scroll_to', { section: 'body > div' }), env)).toMatchObject({ ok: false });
    expect(env.scrollTo).toHaveBeenCalledTimes(1);
  });

  it('opens an item without a detail view by highlighting it', async () => {
    const store = createUIActionStore();
    store.register(Symbol('dashboard'), { page: 'dashboard', items: { email: { highlight: true } } });
    const env = makeEnv();
    expect(await store.execute(request('open_item', { type: 'email', id: 11 }), env)).toMatchObject({ ok: true });
    expect(env.highlight).toHaveBeenCalledWith('email', 11);
  });

  it('prefills only known fields of a registered form, never submitting, and restores them on undo', async () => {
    const store = createUIActionStore();
    const { state, actions } = emailsPage();
    store.register(Symbol('emails'), actions);
    const env = makeEnv();
    const filled = await store.execute(request('prefill_form', { form_id: 'topic.create', fields: { title: 'Garden' } }), env);
    expect(filled).toMatchObject({ ok: true, label: 'topic.create' });
    expect(state.form).toEqual({ title: 'Garden', summary: '' });
    filled.undo?.();
    expect(state.form).toEqual({ title: '', summary: '' });
    for (const params of [
      { form_id: 'login', fields: { password: 'x' } },
      { form_id: 'topic.create', fields: { submit: 'yes' } },
      { form_id: 'topic.create', fields: {} },
      { form_id: 'topic.create', fields: { title: 3 } },
    ]) {
      expect(await store.execute(request('prefill_form', params), env)).toMatchObject({ ok: false });
    }
    expect(state.form).toEqual({ title: '', summary: '' });
  });

  it('waits briefly for a page that registers after a navigation', async () => {
    const store = createUIActionStore();
    const { state, actions } = emailsPage();
    window.setTimeout(() => store.register(Symbol('late'), actions), 150);
    const outcome = await store.execute(
      request('set_filter', { page: 'emails', key: 'priority', value: 'important' }),
      makeEnv({ waitMs: 2000 })
    );
    expect(outcome.ok).toBe(true);
    expect(state.priority).toBe('important');
  });

  it('forgets a page once it unregisters', async () => {
    const store = createUIActionStore();
    const owner = Symbol('emails');
    store.register(owner, emailsPage().actions);
    store.clear(owner);
    expect(await store.execute(request('scroll_to', { section: 'important_emails' }), makeEnv())).toMatchObject({
      ok: false,
    });
  });
});

describe('describeUIActionCall', () => {
  const call = (tool_name: string, args: unknown, tool_id: string | null = 'ui_actions'): ToolCallRecord => ({
    call_id: 'c',
    tool_id,
    tool_name,
    phase: 'end',
    args,
    args_truncated: false,
    result_preview: null,
    result_truncated: false,
    duration_ms: 3,
  });

  it('names a live action by its verb and label, a stored one by its arguments', () => {
    expect(
      describeUIActionCall(call('ui_open_item', { type: 'email', id: 12 }), {
        action: 'open_item',
        label: 'Lunch?',
        ok: true,
        undone: false,
      })
    ).toBe('open_item · Lunch?');
    expect(describeUIActionCall(call('ui_set_filter', { key: 'priority', value: 'spam' }))).toBe(
      'set_filter · priority = spam'
    );
    expect(describeUIActionCall(call('ui_navigate', { route: '/emails' }))).toBe('navigate · /emails');
    expect(describeUIActionCall(call('ui_highlight', { type: 'topic', id: 4 }))).toBe('highlight · topic 4');
    expect(describeUIActionCall(call('search_emails', {}, 'mail'))).toBeNull();
    expect(describeUIActionCall(call('ui_unknown', {}))).toBeNull();
  });
});
