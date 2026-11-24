import fs from 'node:fs';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { E2E_FAKE_NEONIZE_DIR } from './env';
import { api } from './helpers';

/**
 * Drives the fake Neonize client the e2e backend imports instead of the real library
 * (`fake-neonize/neonize/client.py`, JAN-223). A client is named by its uuid: pairing for
 * setup session N and the account it produces both use `setup-N`.
 */

type LogEntry = { event: string; command?: string; session?: string };

type FakeNeonizeCommand =
  | { type: 'qr'; code: string }
  | { type: 'pair_success' }
  | { type: 'pair_error'; error: string }
  | { type: 'connected' }
  | { type: 'disconnected' }
  | { type: 'logged_out' }
  | {
      type: 'message';
      id: string;
      chat: string;
      sender?: string;
      pushname?: string;
      text: string;
      from_me?: boolean;
      timestamp?: number;
    }
  | {
      type: 'history_sync';
      conversations: {
        id: string;
        name?: string;
        unread?: number;
        messages: { id: string; text: string; from_me?: boolean; pushname?: string; participant?: string; timestamp?: number }[];
      }[];
    };

export const clientUuid = (setupSessionId: number) => `setup-${setupSessionId}`;

/** Every lifecycle step and dispatched command the client has logged so far. */
export const fakeNeonizeLog = (uuid: string): LogEntry[] => {
  const file = path.join(E2E_FAKE_NEONIZE_DIR, `${uuid}.log.jsonl`);
  if (!fs.existsSync(file)) return [];
  return fs
    .readFileSync(file, 'utf-8')
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as LogEntry);
};

export const countEvents = (uuid: string, event: string) =>
  fakeNeonizeLog(uuid).filter((entry) => entry.event === event).length;

/** Waits until the client has logged `event` at least `count` times. */
export const waitForEvents = async (uuid: string, event: string, count: number) => {
  await expect.poll(() => countEvents(uuid, event), { timeout: 15_000 }).toBeGreaterThanOrEqual(count);
};

/**
 * Appends a command for the client and waits until it was dispatched. Only a running
 * client reads commands, and only the ones written after it connected.
 */
export const sendToFakeNeonize = async (uuid: string, command: FakeNeonizeCommand) => {
  const dispatchedBefore = countEvents(uuid, 'dispatched');
  fs.mkdirSync(E2E_FAKE_NEONIZE_DIR, { recursive: true });
  fs.appendFileSync(path.join(E2E_FAKE_NEONIZE_DIR, `${uuid}.commands.jsonl`), `${JSON.stringify(command)}\n`);
  await waitForEvents(uuid, 'dispatched', dispatchedBefore + 1);
};

/**
 * Pairs a WhatsApp account through the real setup API and the fake client, without the
 * wizard UI (the QR flow itself is covered in `whatsapp.spec.ts`). Returns the account id
 * and the uuid of its durable connector, which is connected when this returns.
 */
export const pairAccountThroughApi = async (page: Page, displayName: string) => {
  const session = await (
    await api(page, 'post', '/api/whatsapp/setup-sessions', { display_name: displayName, initial_history_days: 3 })
  ).json();
  const uuid = clientUuid(session.id);
  await api(page, 'post', `/api/whatsapp/setup-sessions/${session.id}/start-pairing`);
  await waitForEvents(uuid, 'connect', 1);
  await sendToFakeNeonize(uuid, { type: 'qr', code: `2@e2e-${session.id}` });
  await sendToFakeNeonize(uuid, { type: 'pair_success' });
  await sendToFakeNeonize(uuid, { type: 'connected' });
  // The pairing connection closes itself once paired; the durable connector takes over.
  await waitForEvents(uuid, 'closed', 1);
  await expect
    .poll(async () => (await (await page.request.get(`/api/whatsapp/setup-sessions/${session.id}`)).json()).setup_status)
    .toBe('ready');
  const result = await (await api(page, 'post', `/api/whatsapp/setup-sessions/${session.id}/complete`)).json();
  await waitForEvents(uuid, 'connected', 1);
  return { accountId: result.account.id as number, uuid };
};

/** Deletes every WhatsApp account of the e2e admin (and with it their conversations and messages). */
export const deleteAllWhatsAppAccounts = async (page: Page) => {
  const accounts: { id: number }[] = await (await page.request.get('/api/whatsapp/accounts')).json();
  for (const account of accounts) {
    await api(page, 'delete', `/api/whatsapp/accounts/${account.id}`);
  }
};
