import { type Page, type WebSocketRoute } from '@playwright/test';
import { expect, test } from './fixtures';

// JAN-251: Janus acts on the page the user sees. The e2e account has no LLM and
// no mail, so the mail list comes from fixtures (`page.route`) and the chat
// socket is replayed (`page.routeWebSocket`): it sends what /ws/chat sends for
// page-action tool calls (a tool `start` event, then the `__UI_ACTION__` frame)
// and only finishes each call once the page answered with `ui_action_result`.

const PLACEHOLDER = 'Ask a question...';

const emails = [
  {
    id: 1,
    message_id: '<one@example.com>',
    sender: 'billing@example.com',
    subject: 'Invoice overdue',
    snippet: 'please pay by Friday',
    body: 'the full text',
    priority: 'important',
    received_at: '2026-10-04T08:00:00Z',
    account_id: 1,
    topics: [],
  },
  {
    id: 2,
    message_id: '<two@example.com>',
    sender: 'ana@example.com',
    subject: 'Lunch on Tuesday?',
    snippet: 'are you free',
    body: 'the full text',
    priority: 'digest',
    received_at: '2026-10-04T09:00:00Z',
    account_id: 1,
    topics: [],
  },
];

interface ScriptedAction {
  tool: string;
  action: string;
  params: Record<string, unknown>;
}

/**
 * Replays one reply that runs `steps` in order: each step's call starts, its
 * page action is sent, and the call ends only after the page's result arrives.
 */
const replayPageActions = async (page: Page, steps: ScriptedAction[]) => {
  const results: Array<Record<string, unknown>> = [];
  await page.routeWebSocket(/\/ws\/chat/, (ws: WebSocketRoute) => {
    let index = 0;
    const call = (step: ScriptedAction, position: number) => ({
      call_id: `run-${position}`,
      tool_id: 'ui_actions',
      tool_name: step.tool,
      args: step.params,
      args_truncated: false,
      result_truncated: false,
    });
    const startStep = () => {
      const step = steps[index];
      ws.send('__TOOL_EVENT__:' + JSON.stringify({ ...call(step, index), phase: 'start', result_preview: null, duration_ms: null }));
      ws.send(
        '__UI_ACTION__:' +
          JSON.stringify({
            action_id: `action-${index}`,
            call_id: `run-${index}`,
            action: step.action,
            params: step.params,
            label: String(step.params.route ?? step.action),
            timeout_seconds: 10,
          })
      );
    };
    ws.onMessage((raw) => {
      const frame = JSON.parse(typeof raw === 'string' ? raw : raw.toString()) as Record<string, unknown>;
      if (frame.type === 'ui_action_result') {
        results.push(frame);
        const ok = frame.ok === true;
        ws.send(
          '__TOOL_EVENT__:' +
            JSON.stringify({
              ...call(steps[index], index),
              phase: ok ? 'end' : 'error',
              result_preview: ok ? 'Done.' : `ERROR: the page could not do it: ${String(frame.error)}`,
              duration_ms: 25,
            })
        );
        index += 1;
        if (index < steps.length) {
          startStep();
          return;
        }
        ws.send('Done on your page.');
        ws.send('__END__');
        return;
      }
      ws.send('__CONVERSATION_ID__:252525');
      ws.send('__START__');
      startStep();
    });
  });
  return results;
};

const ask = async (page: Page, question: string) => {
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await input.fill(question);
  await input.press('Enter');
  await expect(sidebar.getByText('Done on your page.')).toBeVisible();
  return sidebar;
};

test('Janus filters the Emails page and opens a mail; both can be undone from their chips', async ({ page }) => {
  await page.route(/\/api\/emails\/\?limit=/, (route) => route.fulfill({ json: emails }));
  const results = await replayPageActions(page, [
    { tool: 'ui_set_filter', action: 'set_filter', params: { page: 'emails', key: 'priority', value: 'important' } },
    { tool: 'ui_open_item', action: 'open_item', params: { type: 'email', id: 1 } },
  ]);

  await page.goto('/emails');
  await expect(page.getByText('Lunch on Tuesday?')).toBeVisible();
  const sidebar = await ask(page, 'show only important mail and open the invoice');

  expect(results).toEqual([
    { type: 'ui_action_result', action_id: 'action-0', ok: true, error: null },
    { type: 'ui_action_result', action_id: 'action-1', ok: true, error: null },
  ]);
  await expect(page).toHaveURL(/\/emails\?email=1$/);
  await expect(page.getByText('Lunch on Tuesday?')).toHaveCount(0);
  await expect(sidebar.getByRole('button', { name: 'set_filter · priority = important, done, 25 ms' })).toBeVisible();
  const opened = 'open_item · Invoice overdue — from billing@example.com';
  await expect(sidebar.getByRole('button', { name: `${opened}, done, 25 ms` })).toBeVisible();

  await sidebar.getByRole('button', { name: `Undo ${opened}` }).click();
  await expect(page).toHaveURL(/\/emails$/);
  await sidebar.getByRole('button', { name: 'Undo set_filter · priority = important' }).click();
  await expect(page.getByText('Lunch on Tuesday?')).toBeVisible();
  await expect(sidebar.getByText('undone')).toHaveCount(2);
});

test('Janus goes to Settings -> Triggers and shows the failed runs, and navigating back undoes it', async ({ page }) => {
  const results = await replayPageActions(page, [
    { tool: 'ui_navigate', action: 'navigate', params: { route: '/settings?tab=runtime' } },
    { tool: 'ui_set_filter', action: 'set_filter', params: { page: 'triggers', key: 'run_status', value: 'failed' } },
  ]);

  await page.goto('/');
  const sidebar = await ask(page, 'go to triggers and show the failed runs');

  expect(results.map((result) => result.ok)).toEqual([true, true]);
  await expect(page).toHaveURL(/\/settings\?tab=runtime/);
  await expect(page.getByRole('heading', { name: 'Trigger definitions' })).toBeVisible();
  await expect(page.getByLabel('Filter runs by status')).toHaveValue('failed');
  await expect(sidebar.getByRole('button', { name: 'navigate · /settings?tab=runtime, done, 25 ms' })).toBeVisible();

  await sidebar.getByRole('button', { name: 'Undo navigate · /settings?tab=runtime' }).click();
  await expect(page).toHaveURL(/\/$/);
});

test('a page action for a route outside the allowlist is rejected and never followed', async ({ page }) => {
  const results = await replayPageActions(page, [
    { tool: 'ui_navigate', action: 'navigate', params: { route: 'https://evil.example.com/' } },
  ]);

  await page.goto('/');
  const sidebar = await ask(page, 'open this link');

  expect(results).toEqual([
    { type: 'ui_action_result', action_id: 'action-0', ok: false, error: 'unknown route https://evil.example.com/' },
  ]);
  await expect(page).toHaveURL(/\/$/);
  await expect(sidebar.getByRole('button', { name: 'navigate · https://evil.example.com/, failed, 25 ms' })).toBeVisible();
});

test('Janus prefills the manual topic form on Memory -> Topics but never submits it', async ({ page }) => {
  const created: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/api\/topics\/?$/.test(new URL(request.url()).pathname)) created.push(request.url());
  });
  const results = await replayPageActions(page, [
    { tool: 'ui_set_filter', action: 'set_filter', params: { page: 'topics', key: 'status', value: 'all' } },
    {
      tool: 'ui_prefill_form',
      action: 'prefill_form',
      params: { form_id: 'topic.create', fields: { title: 'Garden', summary: 'Plans for the garden', marker_keywords: 'seeds, soil' } },
    },
  ]);

  await page.goto('/memory?tab=topics');
  await expect(page.getByRole('heading', { name: 'Tracked topics' })).toBeVisible();
  const sidebar = await ask(page, 'draft a topic about the garden');

  expect(results.map((result) => result.ok)).toEqual([true, true]);
  await expect(page.getByRole('combobox').filter({ hasText: 'Active' })).toHaveValue('all');
  await expect(page.getByLabel('Manual topic title')).toHaveValue('Garden');
  await expect(page.getByLabel('Manual topic description')).toHaveValue('Plans for the garden');
  await expect(page.getByLabel('Manual topic marker keywords')).toHaveValue('seeds, soil');
  expect(created).toEqual([]);

  await sidebar.getByRole('button', { name: 'Undo prefill_form · topic.create' }).click();
  await expect(page.getByLabel('Manual topic title')).toHaveValue('');
});
