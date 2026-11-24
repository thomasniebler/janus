import { expect, test } from './fixtures';
import type { Locator, Page, Route } from '@playwright/test';
import { api } from './helpers';

// JAN-208: the tasks Janus delegated from a conversation are listed above its
// transcript with status, objective, granted tools and result or error. The e2e
// account has no LLM provider, so the live turn replays the /ws/chat frames
// (page.routeWebSocket) and the delegation rows come from a routed response;
// the real endpoint's empty page and ownership 404 are checked against the backend.

const PLACEHOLDER = 'Ask a question...';
const CONVERSATION_ID = 424245;
const REPLY = 'I asked a background task to go through your invoices.';
const DELEGATIONS_URL = `**/api/conversations/${CONVERSATION_ID}/delegations*`;

const runningTask = {
  delegation_id: 'del_e2e_1',
  status: 'running',
  objective: 'Collect this week’s invoices <b>now</b>',
  requested_tool_ids: ['emails', 'emails.send'],
  allowed_tool_ids: ['emails'],
  result: null,
  error: null,
  depth: 1,
  created_at: '2026-10-10T09:00:00Z',
  claimed_at: '2026-10-10T09:00:01Z',
  completed_at: null,
};
type Row = typeof runningTask | Record<string, unknown>;

const page1 = (items: unknown[]) => ({ items, total: items.length, limit: 10, offset: 0, has_more: false });

/** Answers the delegations endpoint like the backend would: newest-first slices of `rows` by `limit`/`offset`. */
const servePaged = (rows: () => Row[]) => (route: Route) => {
  const url = new URL(route.request().url());
  const limit = Number(url.searchParams.get('limit') ?? 20);
  const offset = Number(url.searchParams.get('offset') ?? 0);
  const all = rows();
  const items = all.slice(offset, offset + limit);
  return route.fulfill({
    json: { items, total: all.length, limit, offset, has_more: offset + items.length < all.length },
  });
};

/** Sends one chat message whose replayed turn opens `CONVERSATION_ID`; `onTurn` runs just before the turn ends. */
const chatOneTurn = async (page: Page, onTurn: () => void = () => {}): Promise<Locator> => {
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    ws.onMessage((raw) => {
      const text = typeof raw === 'string' ? raw : raw.toString();
      if (!text.includes('invoices')) return;
      ws.send(`__CONVERSATION_ID__:${CONVERSATION_ID}`);
      ws.send('__TOOL_SELECTION__:' + JSON.stringify({ task_id: 't', step_id: 's', tools: [], denied: [] }));
      ws.send('__START__');
      onTurn();
      ws.send(REPLY);
      ws.send('__END__');
    });
  });

  await page.goto('/');
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await input.fill('Please go through my invoices');
  await input.press('Enter');
  await expect(sidebar.getByText(REPLY)).toBeVisible();
  return sidebar;
};

const many = (count: number): Row[] =>
  Array.from({ length: count }, (_, i) => ({
    ...runningTask,
    delegation_id: `del_page_${i}`,
    status: 'completed',
    objective: `Paged task ${i + 1}`,
    result: `Result ${i + 1}`,
    completed_at: '2026-10-10T09:00:30Z',
  }));

test('a chat lists the tasks it delegated, with live status and result', async ({ page }) => {
  let delegationRows: unknown[] = [];
  await page.route(DELEGATIONS_URL, (route) => route.fulfill({ json: page1(delegationRows) }));
  // The turn delegated work: the row exists by the time the turn ends.
  const sidebar = await chatOneTurn(page, () => {
    delegationRows = [runningTask];
  });

  const panel = sidebar.getByTestId('delegated-tasks');
  await expect(panel.getByText('Delegated tasks (1)')).toBeVisible();
  await expect(panel.getByText('1 active')).toBeVisible();
  await panel.getByText('Delegated tasks (1)').click();

  const item = panel.getByRole('listitem', { name: 'Delegated task: Running' });
  // Model-written objective is plain text: the markup is shown, never rendered.
  await expect(item.getByText('Collect this week’s invoices <b>now</b>')).toBeVisible();
  await expect(item.locator('b')).toHaveCount(0);
  await expect(item.getByText('emails', { exact: true })).toBeVisible();
  await expect(item.getByText(/withheld: emails\.send/)).toBeVisible();

  // While a task is active the panel polls; the finished row replaces it.
  delegationRows = [
    { ...runningTask, status: 'completed', result: 'Three invoices; Acme is the largest.', completed_at: '2026-10-10T09:00:30Z' },
  ];
  const done = panel.getByRole('listitem', { name: 'Delegated task: Completed' });
  await expect(done.getByText('Three invoices; Acme is the largest.')).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText('1 active')).toHaveCount(0);
});

test('each terminal status shows its own badge with the result or the error, and stops polling', async ({ page }) => {
  let requests = 0;
  let delegationRows: unknown[] = [];
  await page.route(DELEGATIONS_URL, (route) => {
    requests += 1;
    return route.fulfill({ json: page1(delegationRows) });
  });
  const sidebar = await chatOneTurn(page, () => {
    delegationRows = [
      {
        ...runningTask,
        delegation_id: 'del_failed',
        status: 'failed',
        objective: 'Check the calendar for conflicts',
        requested_tool_ids: ['calendar'],
        allowed_tool_ids: ['calendar'],
        error: 'calendar connector is not connected',
        completed_at: '2026-10-10T09:01:00Z',
      },
      {
        ...runningTask,
        delegation_id: 'del_interrupted',
        status: 'failed',
        objective: 'Draft the weekly summary',
        requested_tool_ids: [],
        allowed_tool_ids: [],
        error: 'delegation was interrupted before it finished (Janus stopped or the run outlived its lease)',
        completed_at: '2026-10-10T09:00:50Z',
      },
      {
        ...runningTask,
        delegation_id: 'del_completed',
        status: 'completed',
        objective: 'Summarize unread mail from the board',
        requested_tool_ids: ['emails'],
        result: 'Two unread messages, both about the budget.',
        completed_at: '2026-10-10T09:00:40Z',
      },
    ];
  });

  const panel = sidebar.getByTestId('delegated-tasks');
  await expect(panel.getByText('Delegated tasks (3)')).toBeVisible();
  // Nothing is queued or running, so there is no active badge.
  await expect(panel.getByText(/active$/)).toHaveCount(0);
  await panel.getByText('Delegated tasks (3)').click();

  const failed = panel.getByRole('listitem', { name: 'Delegated task: Failed' });
  await expect(failed).toHaveCount(2);
  const connectorFailure = failed.filter({ hasText: 'Check the calendar for conflicts' });
  await expect(connectorFailure.getByText('calendar connector is not connected')).toBeVisible();
  await expect(connectorFailure.getByText('calendar', { exact: true })).toBeVisible();
  const interrupted = failed.filter({ hasText: 'Draft the weekly summary' });
  await expect(interrupted.getByText(/was interrupted before it finished/)).toBeVisible();
  await expect(interrupted.getByText('none', { exact: true })).toBeVisible();

  const completed = panel.getByRole('listitem', { name: 'Delegated task: Completed' });
  await expect(completed.getByText('Two unread messages, both about the budget.')).toBeVisible();
  await expect(completed.getByText(/withheld/)).toHaveCount(0);

  // Only finished tasks: the panel does not keep polling (the active poll is every 5 s).
  const settled = requests;
  await page.waitForTimeout(6_000);
  expect(requests).toBe(settled);
});

test('a conversation without delegated tasks shows no panel', async ({ page }) => {
  const seen: string[] = [];
  await page.route(DELEGATIONS_URL, (route) => {
    seen.push(route.request().url());
    return route.fulfill({ json: page1([]) });
  });
  const sidebar = await chatOneTurn(page);

  // The list was read for the conversation once its turn ended, and it was empty.
  await expect.poll(() => seen.length).toBeGreaterThan(0);
  await expect(sidebar.getByTestId('delegated-tasks')).toHaveCount(0);
  await expect(sidebar.getByText(/Delegated tasks/)).toHaveCount(0);
});

test('a failed load says so instead of showing a stale or empty list', async ({ page }) => {
  await page.route(DELEGATIONS_URL, (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }));
  const sidebar = await chatOneTurn(page);

  // TanStack Query's default three retries back off for about 7 s before the error shows.
  await expect(sidebar.getByText('Delegated tasks could not be loaded.')).toBeVisible({ timeout: 20_000 });
  await expect(sidebar.getByTestId('delegated-tasks')).toHaveCount(0);
});

test('"Show more" pages through the delegated tasks newest first', async ({ page }) => {
  const rows = many(25);
  await page.route(DELEGATIONS_URL, servePaged(() => rows));
  const sidebar = await chatOneTurn(page);

  const panel = sidebar.getByTestId('delegated-tasks');
  await expect(panel.getByText('Delegated tasks (25)')).toBeVisible();
  await panel.getByText('Delegated tasks (25)').click();
  const items = panel.getByRole('listitem');
  await expect(items).toHaveCount(10);
  await expect(items.first()).toContainText('Paged task 1');

  const showMore = panel.getByRole('button', { name: 'Show more' });
  await showMore.click();
  await expect(items).toHaveCount(20);
  await showMore.click();
  await expect(items).toHaveCount(25);
  await expect(items.last()).toContainText('Paged task 25');
  // Everything is shown: no further page to ask for.
  await expect(showMore).toHaveCount(0);
  await expect(panel.getByText(/^Showing the newest/)).toHaveCount(0);
});

test('past the endpoint ceiling the panel says how many it is not showing', async ({ page }) => {
  const rows = many(120);
  await page.route(DELEGATIONS_URL, servePaged(() => rows));
  const sidebar = await chatOneTurn(page);

  const panel = sidebar.getByTestId('delegated-tasks');
  await panel.getByText('Delegated tasks (120)').click();
  const items = panel.getByRole('listitem');
  const showMore = panel.getByRole('button', { name: 'Show more' });
  for (let shown = 10; shown < 100; shown += 10) {
    await expect(items).toHaveCount(shown);
    await showMore.click();
  }
  await expect(items).toHaveCount(100);
  await expect(showMore).toHaveCount(0);
  await expect(panel.getByText('Showing the newest 100 of 120.')).toBeVisible();
});

test('the delegations endpoint is owner-scoped, paginated, and empty for a fresh conversation', async ({ page }) => {
  await page.goto('/');
  const created = await (await api(page, 'post', '/api/conversations/', { title: 'Delegations e2e' })).json();

  const own = await page.request.get(`/api/conversations/${created.id}/delegations`);
  expect(own.status()).toBe(200);
  expect(await own.json()).toEqual({ items: [], total: 0, limit: 20, offset: 0, has_more: false });

  const paged = await page.request.get(`/api/conversations/${created.id}/delegations?limit=5&offset=10`);
  expect(await paged.json()).toEqual({ items: [], total: 0, limit: 5, offset: 10, has_more: false });

  for (const query of ['limit=0', 'limit=101', 'offset=-1', 'offset=9223372036854775808']) {
    const bad = await page.request.get(`/api/conversations/${created.id}/delegations?${query}`);
    expect(bad.status(), query).toBe(422);
  }

  const missing = await page.request.get('/api/conversations/987654321/delegations');
  expect(missing.status()).toBe(404);

  await api(page, 'delete', `/api/conversations/${created.id}`);
});
