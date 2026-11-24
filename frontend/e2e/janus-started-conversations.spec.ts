import type { Locator, Page } from '@playwright/test';
import { lastJsonLine, runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import { api, createThrowawayUser, signInAs, type E2EUser } from './helpers';

const FLIGHT = 'E2E flight to Lisbon moved';
const FLIGHT_MESSAGE = 'Your Friday flight now leaves at 09:40. Should I move the airport transfer?';
const INVOICE = 'E2E invoice from Acme overdue';

/**
 * Seeds two conversations as a trigger run would start them with `start_conversation`
 * (JAN-319), through the real service. Starting one needs a live model in a trigger
 * run, so the service is called directly; everything below runs against the real
 * backend and UI. Each call names its own (fake) run ids, since one run starts at most one
 * conversation. Prints the new conversation ids.
 */
const SEED_SCRIPT = `
import json
from janus.services.conversation_service import ConversationService

service = ConversationService(db)
ids = [
    service.start_janus_conversation(
        user.id, title=title, message=message, trigger_id=f"e2e-jan-319-{args[1]}-{index}"
    ).id
    for index, (title, message) in enumerate(json.loads(args[0]))
]
print(json.dumps(ids))
`;

/** Seeds Janus-started conversations for `owner`; returns their ids. */
const seed = async (owner: E2EUser, conversations: [string, string][], runId: string): Promise<number[]> =>
  lastJsonLine<number[]>(await runBackendPython(SEED_SCRIPT, [JSON.stringify(conversations), runId], { as: owner.username }));

/** One entry of a conversation history list (the /chat page's or the sidebar's). */
const historyEntry = (scope: Locator, title: string): Locator =>
  scope.getByRole('button').filter({ has: scope.page().getByText(title, { exact: true }) });

const chatNavBadge = (page: Page, label: string) =>
  page.getByRole('link', { name: 'Chat', exact: false }).getByLabel(label).first();

/** Resolves once this page's /ws/changes socket is subscribed (its "ready" frame arrived). */
const changesSubscribed = (page: Page) =>
  new Promise<void>((resolve) => {
    page.on('websocket', (ws) => {
      if (!ws.url().endsWith('/ws/changes')) return;
      ws.on('framereceived', (frame) => {
        if (String(frame.payload).includes('"ready"')) resolve();
      });
    });
  });

// The tests share one throwaway user (JAN-336) and run in order: the badge counts and the
// "no badge" checks are this user's own, never the shared e2e admin's, so nothing needs
// cleaning up and no other spec's conversations count.
test.describe('Janus-started conversations (JAN-319)', () => {
  let seededIds: number[] = [];
  let owner: E2EUser;

  test.beforeAll(async ({ adminRequest }) => {
    owner = await createThrowawayUser(adminRequest, 'e2e-janus-started');
    seededIds = await seed(
      owner,
      [
        [FLIGHT, FLIGHT_MESSAGE],
        [INVOICE, 'The Acme invoice is 10 days overdue. Want me to draft a reminder?'],
      ],
      'initial'
    );
    expect(seededIds).toHaveLength(2);
  });

  test.beforeEach(async ({ page }) => {
    await signInAs(page, owner);
  });

  test('appear in the sidebar chat history marked as started by Janus and new, with a Chat badge', async ({ page }) => {
    await page.goto('/');
    const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });

    for (const title of [FLIGHT, INVOICE]) {
      const entry = historyEntry(sidebar, title);
      await expect(entry).toBeVisible();
      await expect(entry.getByTestId('janus-started-marker')).toHaveText(/Started by Janus/);
      await expect(entry.getByText('New', { exact: true })).toBeVisible();
    }
    await expect(chatNavBadge(page, '2 new conversations from Janus')).toBeVisible();
  });

  for (const shell of ['v2', 'v3'] as const) {
    test(`the ${shell} shell badges its Chat entry and marks the history too`, async ({ page }) => {
      await page.goto(`/chat?ui=${shell}`);
      await expect(page.locator('a[href="/chat"]').getByLabel('2 new conversations from Janus').first()).toBeVisible();
      const entry = historyEntry(page.getByTestId('chat-page'), FLIGHT);
      await expect(entry.getByTestId('janus-started-marker')).toHaveText(/Started by Janus/);
      await expect(entry.getByText('New', { exact: true })).toBeVisible();
    });
  }

  test('opens with Janus’s message, and New and the badge clear once opened, across a reload', async ({ page }) => {
    await page.goto('/chat');
    const chatPage = page.getByTestId('chat-page');
    const flight = historyEntry(chatPage, FLIGHT);
    await expect(flight.getByText('New', { exact: true })).toBeVisible();

    await flight.click();

    await expect(flight).toHaveAttribute('aria-current', 'true');
    const transcript = chatPage.getByRole('region', { name: 'Conversation history' });
    await expect(transcript.getByText(FLIGHT_MESSAGE)).toBeVisible();
    await expect(transcript.getByTestId('janus-started-note')).toContainText('Janus started this conversation');
    await expect(flight.getByText('New', { exact: true })).toHaveCount(0);
    await expect(flight.getByTestId('janus-started-marker')).toHaveText('Started by Janus');
    // The other one is still unread.
    await expect(historyEntry(chatPage, INVOICE).getByText('New', { exact: true })).toBeVisible();
    await expect(chatNavBadge(page, '1 new conversation from Janus')).toBeVisible();

    await page.reload();
    const reloaded = historyEntry(page.getByTestId('chat-page'), FLIGHT);
    await expect(reloaded.getByTestId('janus-started-marker')).toHaveText('Started by Janus');
    await expect(reloaded.getByText('New', { exact: true })).toHaveCount(0);
    await expect(page.getByLabel(/new conversations? from Janus/).first()).toHaveAttribute(
      'aria-label',
      '1 new conversation from Janus'
    );
  });

  test('opening the last new one removes the Chat badge entirely', async ({ page }) => {
    await page.goto('/chat');
    const chatPage = page.getByTestId('chat-page');
    await historyEntry(chatPage, INVOICE).click();

    await expect(chatPage.getByText('Want me to draft a reminder?', { exact: false })).toBeVisible();
    await expect(page.getByLabel(/new conversations? from Janus/)).toHaveCount(0);
  });

  test('opening one in another tab clears New and the badge on the open page without a reload', async ({ page }) => {
    const title = 'E2E parcel held at customs';
    const [id] = await seed(owner, [[title, 'Customs needs an invoice for the parcel. Shall I find it?']], 'live');
    seededIds.push(id);
    const subscribed = changesSubscribed(page);
    await page.goto('/chat');
    const entry = historyEntry(page.getByTestId('chat-page'), title);
    await expect(entry.getByText('New', { exact: true })).toBeVisible();
    await expect(chatNavBadge(page, '1 new conversation from Janus')).toBeVisible();
    await subscribed;

    // Another tab (here: a plain API read) opens it; the server's conversations domain change refreshes this page.
    const opened = await page.request.get(`/api/conversations/${id}`);
    expect(opened.ok()).toBeTruthy();
    expect(await opened.json()).toMatchObject({ origin: 'janus', unread: false });

    await expect(entry.getByText('New', { exact: true })).toHaveCount(0);
    await expect(page.getByLabel(/new conversations? from Janus/)).toHaveCount(0);
    await expect(entry.getByTestId('janus-started-marker')).toHaveText('Started by Janus');
  });

  test('the Start conversations tool has its own Unattended switch, Allowed by default', async ({ page }) => {
    await page.goto('/settings?tab=tools');
    const tools = page.getByRole('list', { name: 'Built-in tool permissions' });
    const unattended = tools.getByRole('combobox', { name: 'Unattended: Start conversations' });
    await expect(unattended).toHaveValue('allowed');

    // Off is a restrictive change: no warning, saved across a reload.
    await unattended.selectOption('off');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(tools.getByRole('button', { name: 'Reset Start conversations' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'Unattended: Start conversations' })).toHaveValue('off');

    await page.getByRole('button', { name: 'Reset Start conversations' }).click();
    await expect(page.getByRole('combobox', { name: 'Unattended: Start conversations' })).toHaveValue('allowed');
    await expect(page.getByRole('button', { name: 'Reset Start conversations' })).toHaveCount(0);
  });

  test('a conversation the user started is not marked', async ({ page }) => {
    const title = `E2E my own chat ${Date.now()}`;
    const created = await (await api(page, 'post', '/api/conversations/', { title })).json();
    try {
      expect(created).toMatchObject({ origin: 'user', unread: false });
      await page.goto('/chat');
      const entry = historyEntry(page.getByTestId('chat-page'), title);
      await expect(entry).toBeVisible();
      await expect(entry.getByTestId('janus-started-marker')).toHaveCount(0);
      await entry.click();
      await expect(page.getByTestId('janus-started-note')).toHaveCount(0);
    } finally {
      await api(page, 'delete', `/api/conversations/${created.id}`);
    }
  });

  test('the API lists them with their origin, all read after they were opened', async ({ page }) => {
    const listed = await (await page.request.get('/api/conversations/')).json();
    const seeded = listed.filter((conversation: { id: number }) => seededIds.includes(conversation.id));
    expect(seeded.map((conversation: { title: string; origin: string; unread: boolean }) => [
      conversation.title,
      conversation.origin,
      conversation.unread,
    ])).toEqual(
      expect.arrayContaining([
        [FLIGHT, 'janus', false],
        [INVOICE, 'janus', false],
        ['E2E parcel held at customs', 'janus', false],
      ])
    );
  });
});
