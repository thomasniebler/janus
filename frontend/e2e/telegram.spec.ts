import { startFakeUpstream, type FakeUpstream, type RecordedRequest } from './fake-upstream';
import { E2E_FAKE_UPSTREAM_PORT } from './env';
import { expect, test } from './fixtures';
import { api } from './helpers';

// JAN-405: the Telegram connector against a fake Bot API on the fixed fake-upstream port
// (JANUS_TELEGRAM_API_BASE_URL points there). The user connects a bot in Settings ->
// Connections, sends /start <code> from their chat (queued in the fake), sees it linked, and
// sends a test message, which reaches only the linked chat. Runs as a throwaway user.

test.use({ asThrowawayUser: true });

const TOKEN = '424242:E2E_fake-telegram-token';
const CHAT_ID = 777001;

let upstream: FakeUpstream;
let queued: Record<string, unknown>[] = [];

const handler = (request: RecordedRequest) => {
  const match = /^\/telegram\/bot([^/]+)\/(\w+)$/.exec(request.url);
  if (!match) return undefined;
  const [, token, method] = match;
  if (token !== TOKEN) return { status: 401, json: { ok: false, description: 'Unauthorized' } };
  if (method === 'getMe') return { json: { ok: true, result: { id: 1, is_bot: true, username: 'janus_e2e_bot' } } };
  if (method === 'getUpdates') {
    const offset = Number((JSON.parse(request.body || '{}') as { offset?: number }).offset ?? 0);
    return { json: { ok: true, result: queued.filter((update) => Number(update.update_id) >= offset) } };
  }
  if (method === 'sendMessage') return { json: { ok: true, result: { message_id: 5 } } };
  return { status: 404, json: { ok: false } };
};

const sentMessages = () =>
  upstream.requests
    .filter((request) => request.url.endsWith('/sendMessage'))
    .map((request) => JSON.parse(request.body) as { chat_id: string; text: string });

test.beforeAll(async () => {
  upstream = await startFakeUpstream(E2E_FAKE_UPSTREAM_PORT, handler);
});
test.afterAll(async () => {
  await upstream.close();
});

test('connect a bot, link your chat with the pairing code, and send a test message', async ({ page }) => {
  queued = [];
  await page.goto('/settings?tab=connections&sub=messaging');
  const card = page.getByTestId('telegram-config');
  await card.getByLabel('Bot token').fill('999:WRONG');
  await card.getByRole('button', { name: 'Connect' }).click();
  await expect(card).toContainText('Telegram rejected the bot token');

  await card.getByLabel('Bot token').fill(TOKEN);
  await card.getByRole('button', { name: 'Connect' }).click();
  const pairing = card.getByTestId('telegram-pairing');
  await expect(pairing).toContainText('/start ');
  const code = /\/start (\d{8})/.exec((await pairing.textContent()) ?? '')?.[1];
  expect(code).toMatch(/^\d{8}$/);
  expect(await (await page.request.get('/api/telegram')).text()).not.toContain(TOKEN);

  // A stranger's message is ignored; the owner's /start links the chat.
  queued = [
    { update_id: 1, message: { message_id: 1, date: 1764100000, chat: { id: 1234, type: 'private' }, text: '/start 00000000' } },
    { update_id: 2, message: { message_id: 2, date: 1764100001, chat: { id: CHAT_ID, type: 'private' }, text: `/start ${code}` } },
  ];
  await expect(card).toContainText('linked to your chat', { timeout: 30_000 });
  expect(sentMessages()).toEqual([{ chat_id: String(CHAT_ID), text: 'Linked to Janus. Messages you send here can start your Telegram triggers.' }]);

  await card.getByRole('button', { name: 'Send test message' }).click();
  await expect(card).toContainText('Test message sent.');
  expect(sentMessages().at(-1)).toEqual({ chat_id: String(CHAT_ID), text: 'Test message from Janus.' });

  await card.getByRole('button', { name: 'Disconnect' }).click();
  await expect(card.getByLabel('Bot token')).toBeVisible();
  await api(page, 'get', '/api/telegram');
});
