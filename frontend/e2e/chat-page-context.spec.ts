import { type Page } from '@playwright/test';
import { expect, test } from './fixtures';

// JAN-250: every chat message carries a structured snapshot of the page the
// user is on. The e2e account has no LLM and no mail, so the mail lists are
// served from fixtures (`page.route`) and the chat socket is replayed
// (`page.routeWebSocket`) to capture exactly what the client sends.

const PLACEHOLDER = 'Ask a question...';

const emails = [
  {
    id: 1,
    message_id: '<one@example.com>',
    sender: 'billing@example.com',
    subject: 'Invoice overdue',
    snippet: 'SNIPPET-ONE please pay by Friday',
    body: 'BODY-ONE the full confidential text',
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
    snippet: 'SNIPPET-TWO are you free',
    body: 'BODY-TWO the full text',
    priority: 'digest',
    received_at: '2026-10-04T09:00:00Z',
    account_id: 1,
    topics: [],
  },
];

/** Captures every frame the client sends on /ws/chat and answers each message with a short reply. */
const captureChatFrames = async (page: Page) => {
  const sent: Array<Record<string, unknown>> = [];
  await page.routeWebSocket(/\/ws\/chat/, (ws) => {
    ws.onMessage((raw) => {
      const frame = JSON.parse(typeof raw === 'string' ? raw : raw.toString());
      sent.push(frame);
      ws.send('__CONVERSATION_ID__:515151');
      ws.send('__START__');
      ws.send('You are looking at a page.');
      ws.send('__END__');
    });
  });
  return sent;
};

const ask = async (page: Page, question: string) => {
  const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
  const input = sidebar.getByPlaceholder(PLACEHOLDER);
  await expect(input).toBeVisible();
  await input.fill(question);
  // Right after a navigation the chat socket may still be connecting, and a message sent
  // before it opens stays in the input. Press Enter until it is sent: a sent message clears
  // the input, so this never sends twice.
  await expect(async () => {
    if (await input.inputValue()) await input.press('Enter');
    await expect(input).toHaveValue('', { timeout: 500 });
  }).toPass();
  await expect(sidebar.getByText('You are looking at a page.').last()).toBeVisible();
};

test('the Emails page sends its filter, listed emails, and the open email, never their text', async ({ page }) => {
  await page.route(/\/api\/emails\/\?limit=/, (route) => route.fulfill({ json: emails }));
  const sent = await captureChatFrames(page);

  await page.goto('/emails?email=1');
  await expect(page.getByText('Lunch on Tuesday?').first()).toBeVisible();
  await ask(page, 'what am I looking at?');

  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({
    type: 'message',
    text: 'what am I looking at?',
    page_context: {
      page: 'Emails',
      route: '/emails?email=1',
      filters: { priority: 'all' },
      selection: { type: 'email', id: 1, label: 'Invoice overdue — from billing@example.com' },
      visible_items: [
        { type: 'email', id: 1, label: 'Invoice overdue — from billing@example.com' },
        { type: 'email', id: 2, label: 'Lunch on Tuesday? — from ana@example.com' },
      ],
    },
  });
  const frame = JSON.stringify(sent[0]);
  expect(frame).not.toContain('SNIPPET');
  expect(frame).not.toContain('BODY');
  expect(new TextEncoder().encode(JSON.stringify(sent[0].page_context)).length).toBeLessThanOrEqual(2048);
});

test('the Dashboard sends its widgets and their items, and other pages at least their route', async ({ page }) => {
  await page.route(/\/api\/dashboard\/important-emails/, (route) => route.fulfill({ json: [emails[0]] }));
  const sent = await captureChatFrames(page);

  await page.goto('/');
  await expect(page.getByText('Invoice overdue').first()).toBeVisible();
  await ask(page, 'what am I looking at?');

  const dashboard = sent[0].page_context as { page: string; route: string; filters: { widgets: string }; visible_items: unknown[] };
  expect(dashboard.page).toBe('Dashboard');
  expect(dashboard.route).toBe('/');
  expect(dashboard.filters.widgets).toContain('important_emails');
  expect(dashboard.visible_items).toContainEqual({
    type: 'email',
    id: 1,
    label: 'Invoice overdue — from billing@example.com',
  });
  expect(JSON.stringify(dashboard)).not.toContain('SNIPPET');

  // Switching pages switches the snapshot: Settings names its open tab.
  await page.goto('/settings?tab=runtime');
  await expect(page.getByRole('heading', { name: 'Trigger definitions' })).toBeVisible();
  await ask(page, 'and now?');
  expect(sent[1].page_context).toMatchObject({ page: 'Settings › Triggers', filters: { tab: 'runtime' } });
});
