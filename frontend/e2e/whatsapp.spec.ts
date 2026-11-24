import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { answerConfirm, api, confirmDialog } from './helpers';
import {
  clientUuid,
  countEvents,
  deleteAllWhatsAppAccounts,
  fakeNeonizeLog,
  pairAccountThroughApi,
  sendToFakeNeonize,
  waitForEvents,
} from './fakeNeonize';

/*
 * WhatsApp connector (JAN-223). The e2e backend imports a scripted fake in place of the
 * Neonize client library (see `fakeNeonize.ts`), so these specs drive pairing events and
 * messages themselves while the connector, setup service, API and UI run for real. No
 * WhatsApp connection is ever opened.
 */

const minutesAgo = (minutes: number) => Date.now() / 1000 - minutes * 60;

const openWhatsAppSettings = async (page: Page) => {
  await page.goto('/settings?tab=whatsapp');
  await expect(page.getByRole('heading', { name: 'WhatsApp Setup' })).toBeVisible();
};

const accountCard = (page: Page, displayName: string) =>
  page.locator('div.p-4').filter({ has: page.getByRole('heading', { name: displayName, exact: true }) });

test.describe('WhatsApp connector', () => {
  test.afterEach(async ({ page }) => {
    await deleteAllWhatsAppAccounts(page);
  });

  test('pairs an account by QR code and hands off to the durable connector', async ({ page }) => {
    const displayName = `E2E Phone ${Date.now()}`;
    await openWhatsAppSettings(page);
    await expect(page.getByText('No WhatsApp accounts configured yet.')).toBeVisible();

    await page.getByLabel('Display Name').fill(displayName);
    const created = page.waitForResponse(
      (response) => response.url().endsWith('/api/whatsapp/setup-sessions') && response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Start setup' }).click();
    const sessionId: number = (await (await created).json()).id;
    const uuid = clientUuid(sessionId);

    await expect(page.getByRole('heading', { name: 'Pair in Janus' })).toBeVisible();
    const finish = page.getByRole('button', { name: 'Finish setup' });
    await expect(finish).toBeDisabled();

    // Pending: the pairing connector starts and waits for WhatsApp's QR code.
    await page.getByRole('button', { name: 'Start pairing' }).click();
    await expect(page.getByText('Starting live Neonize pairing.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start pairing' })).toBeDisabled();
    await waitForEvents(uuid, 'connect', 1);

    await sendToFakeNeonize(uuid, { type: 'qr', code: `2@e2e-pairing-${sessionId}` });
    await expect(page.getByText('Scan the QR code with WhatsApp on your phone.')).toBeVisible();
    const qr = page.getByRole('img', { name: 'Scan with WhatsApp on your phone' });
    await expect(qr).toBeVisible();
    await expect(qr).toHaveAttribute('src', /^data:image\/png;base64,/);
    await expect(finish).toBeDisabled();

    await sendToFakeNeonize(uuid, { type: 'pair_success' });
    await expect(page.getByText('Pairing accepted. Finishing connection setup.')).toBeVisible();
    await expect(finish).toBeDisabled();

    // Paired: the pairing-time connection closes itself instead of staying up.
    await sendToFakeNeonize(uuid, { type: 'connected' });
    await expect(page.getByText('Pairing complete.')).toBeVisible();
    await expect(finish).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Start pairing' })).toBeDisabled();
    await waitForEvents(uuid, 'closed', 1);
    expect(countEvents(uuid, 'connect')).toBe(1);

    await finish.click();
    await expect(
      page.getByText(
        `Connected ${displayName}. WhatsApp connector started. Messages will sync automatically as they arrive.`,
      ),
    ).toBeVisible();
    // The wizard resets to its intro step.
    await expect(page.getByRole('button', { name: 'Start setup' })).toBeVisible();

    // The durable per-account connector reopens the paired session (same store, same
    // client id) and connects on its own.
    await waitForEvents(uuid, 'connected', 1);
    const connects = fakeNeonizeLog(uuid).filter((entry) => entry.event === 'connect');
    expect(connects).toHaveLength(2);
    expect(connects[1].session).toBe(connects[0].session);

    const card = accountCard(page, displayName);
    await expect(card.getByText('whatsapp_neonize · connected · initial sync 3 day(s)')).toBeVisible();
    await expect(page.getByText('No WhatsApp accounts configured yet.')).toHaveCount(0);

    await card.getByRole('button', { name: 'Test' }).click();
    await expect(card.getByText('Neonize session configuration is present.')).toBeVisible();
  });

  test('shows a rejected pairing with the fallback import, then cancels setup', async ({ page }) => {
    await openWhatsAppSettings(page);
    await page.getByLabel('Display Name').fill(`E2E Rejected ${Date.now()}`);
    const created = page.waitForResponse(
      (response) => response.url().endsWith('/api/whatsapp/setup-sessions') && response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Start setup' }).click();
    const uuid = clientUuid((await (await created).json()).id);

    await page.getByRole('button', { name: 'Start pairing' }).click();
    await waitForEvents(uuid, 'connect', 1);
    await sendToFakeNeonize(uuid, { type: 'qr', code: '2@e2e-rejected' });
    await expect(page.getByRole('img', { name: 'Scan with WhatsApp on your phone' })).toBeVisible();

    await sendToFakeNeonize(uuid, { type: 'pair_error', error: 'the phone rejected the code' });
    await expect(page.getByText('Pairing failed: the phone rejected the code.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Fallback import' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Finish setup' })).toBeDisabled();
    // A failed attempt can be retried.
    await expect(page.getByRole('button', { name: 'Start pairing' })).toBeEnabled();

    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(page.getByText('Setup was cancelled.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Finish setup' })).toBeDisabled();
    // Cancelling stops the pairing connection; no account was created.
    await waitForEvents(uuid, 'closed', 1);
    await openWhatsAppSettings(page);
    await expect(page.getByText('No WhatsApp accounts configured yet.')).toBeVisible();
  });

  test('lists synced messages of a paired account on the dashboard', async ({ page }) => {
    const { uuid } = await pairAccountThroughApi(page, `E2E Messages ${Date.now()}`);

    // The one-time backlog push after pairing: a direct chat with an incoming and an
    // outgoing message, and one message older than the 3-day window.
    await sendToFakeNeonize(uuid, {
      type: 'history_sync',
      conversations: [
        {
          id: '4915100000001@s.whatsapp.net',
          name: 'Ada Lovelace',
          unread: 1,
          messages: [
            { id: 'hist-in', text: 'Are we still on for the review?', pushname: 'Ada Lovelace', timestamp: minutesAgo(30) },
            { id: 'hist-out', text: 'Yes, 3pm works.', from_me: true, timestamp: minutesAgo(20) },
            { id: 'hist-old', text: 'Message from last week', pushname: 'Ada Lovelace', timestamp: minutesAgo(10 * 24 * 60) },
          ],
        },
      ],
    });
    // A live group message.
    await sendToFakeNeonize(uuid, {
      type: 'message',
      id: 'live-group',
      chat: '120363000000000001@g.us',
      sender: '4915100000002@s.whatsapp.net',
      pushname: 'Grace Hopper',
      text: 'Standup moved to 10:15',
      timestamp: minutesAgo(5),
    });

    await page.goto('/');
    const widget = page.getByTestId('dashboard-widget-whatsapp_messages');
    await expect(widget.getByText('3 Recent')).toBeVisible();

    const rows = widget.locator('div.p-4');
    await expect(rows).toHaveCount(3);
    // Newest first.
    await expect(rows.nth(0)).toContainText('Grace Hopper');
    await expect(rows.nth(0)).toContainText('Standup moved to 10:15');
    await expect(rows.nth(0)).toContainText('incoming');
    await expect(rows.nth(1)).toContainText('Yes, 3pm works.');
    await expect(rows.nth(1)).toContainText('outgoing');
    await expect(rows.nth(2)).toContainText('Ada Lovelace');
    await expect(rows.nth(2)).toContainText('Are we still on for the review?');
    await expect(rows.nth(2)).toContainText('digest');
    await expect(widget.getByText('Message from last week')).toHaveCount(0);

    // A message arriving while the dashboard is open shows up without a reload.
    await sendToFakeNeonize(uuid, {
      type: 'message',
      id: 'live-direct',
      chat: '4915100000001@s.whatsapp.net',
      pushname: 'Ada Lovelace',
      text: 'See you there',
    });
    await expect(widget.getByText('See you there')).toBeVisible();
    await expect(widget.getByText('4 Recent')).toBeVisible();

    const conversations: { title: string; conversation_type: string }[] = await (
      await page.request.get('/api/whatsapp/conversations')
    ).json();
    expect(conversations.map((conversation) => [conversation.title, conversation.conversation_type])).toEqual([
      ['Ada Lovelace', 'direct'],
      ['120363000000000001', 'group'],
    ]);
  });

  test('restarts a dropped connector without redelivering history', async ({ page }) => {
    const displayName = `E2E Restart ${Date.now()}`;
    const { uuid } = await pairAccountThroughApi(page, displayName);
    await sendToFakeNeonize(uuid, {
      type: 'message',
      id: 'before-restart',
      chat: '4915100000003@s.whatsapp.net',
      pushname: 'Alan Turing',
      text: 'Before the restart',
      timestamp: minutesAgo(10),
    });

    await openWhatsAppSettings(page);
    const card = accountCard(page, displayName);
    await expect(card.getByText('whatsapp_neonize · connected · initial sync 3 day(s)')).toBeVisible();

    // The connection drops: the account turns degraded while the page is open.
    await sendToFakeNeonize(uuid, { type: 'disconnected' });
    await expect(card.getByText('whatsapp_neonize · degraded · initial sync 3 day(s)')).toBeVisible();
    await expect(card.getByText('Disconnected from WhatsApp.')).toBeVisible();

    await card.getByRole('button', { name: 'Restart connector' }).click();
    const notice = card.getByText('WhatsApp connector restarted. New messages will sync automatically as they arrive.');
    await expect(notice).toBeVisible();
    // It promises new messages only, never a resync of past ones.
    await expect(notice).not.toContainText(/resync|history|redeliver/i);

    // The old connection is closed and a new one reopens the same session.
    await waitForEvents(uuid, 'connected', 2);
    expect(countEvents(uuid, 'disconnect')).toBeGreaterThanOrEqual(2);
    await expect(card.getByText('whatsapp_neonize · connected · initial sync 3 day(s)')).toBeVisible();
    await expect(card.getByText('Disconnected from WhatsApp.')).toHaveCount(0);

    // Nothing was redelivered, and the new connection ingests live messages again.
    const messagesAfterRestart: { snippet: string }[] = await (await page.request.get('/api/whatsapp/messages')).json();
    expect(messagesAfterRestart.map((message) => message.snippet)).toEqual(['Before the restart']);
    await sendToFakeNeonize(uuid, {
      type: 'message',
      id: 'after-restart',
      chat: '4915100000003@s.whatsapp.net',
      pushname: 'Alan Turing',
      text: 'After the restart',
    });
    await page.goto('/');
    const widget = page.getByTestId('dashboard-widget-whatsapp_messages');
    await expect(widget.getByText('After the restart')).toBeVisible();
    await expect(widget.getByText('Before the restart')).toBeVisible();
    await expect(widget.getByText('2 Recent')).toBeVisible();
  });

  test('a disabled account refuses a restart, and deleting it removes its messages', async ({ page }) => {
    const displayName = `E2E Delete ${Date.now()}`;
    const { accountId, uuid } = await pairAccountThroughApi(page, displayName);
    await sendToFakeNeonize(uuid, {
      type: 'message',
      id: 'to-be-deleted',
      chat: '4915100000004@s.whatsapp.net',
      pushname: 'Katherine Johnson',
      text: 'Delete me with the account',
    });

    // Disabling (there is no UI switch for it) stops the live connection.
    await api(page, 'patch', `/api/whatsapp/accounts/${accountId}`, { is_enabled: false });
    await waitForEvents(uuid, 'closed', 2);

    await openWhatsAppSettings(page);
    const card = accountCard(page, displayName);
    await card.getByRole('button', { name: 'Restart connector' }).click();
    await expect(card.getByText('This WhatsApp account is disabled.')).toBeVisible();
    const connectsWhileDisabled = countEvents(uuid, 'connect');
    expect(connectsWhileDisabled).toBe(2);

    const deleteButton = page.getByRole('button', { name: `Delete WhatsApp account "${displayName}"` });
    await deleteButton.click();
    await expect(confirmDialog(page, 'Delete WhatsApp account?')).toContainText(displayName);
    await answerConfirm(page, 'Delete WhatsApp account?', 'Cancel');
    await expect(card).toBeVisible();

    await deleteButton.click();
    await answerConfirm(page, 'Delete WhatsApp account?', 'Delete');
    await expect(page.getByText('No WhatsApp accounts configured yet.')).toBeVisible();
    await expect(page.getByRole('heading', { name: displayName, exact: true })).toHaveCount(0);

    await page.goto('/');
    const widget = page.getByTestId('dashboard-widget-whatsapp_messages');
    await expect(widget.getByText('No recent WhatsApp messages yet.')).toBeVisible();
    await expect(widget.getByText('Delete me with the account')).toHaveCount(0);
    expect(countEvents(uuid, 'connect')).toBe(connectsWhileDisabled);
  });
});
