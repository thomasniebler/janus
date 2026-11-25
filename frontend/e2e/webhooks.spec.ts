import { createHmac } from 'node:crypto';
import { request as playwrightRequest } from '@playwright/test';
import { E2E_BASE_URL } from './env';
import { expect, test } from './fixtures';
import { api } from './helpers';

// JAN-406: inbound webhooks. The user creates a webhook on Settings -> Runtime triggers and sees its
// URL and secret once. An outside sender, with no session, then POSTs signed JSON: a Janus-style
// signature starts the user's "Webhook received" trigger; an unsigned request is refused; a
// GitHub-style signature works too. Every test runs as its own throwaway user.

test.use({ asThrowawayUser: true });

const janusHeaders = (secret: string, body: string) => {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = createHmac('sha256', secret).update(`${timestamp}.${body}`).digest('hex');
  return { 'Content-Type': 'application/json', 'X-Janus-Timestamp': timestamp, 'X-Janus-Signature': `sha256=${signature}` };
};

const hubHeaders = (secret: string, body: string) => ({
  'Content-Type': 'application/json',
  'X-GitHub-Event': 'push',
  'X-Hub-Signature-256': `sha256=${createHmac('sha256', secret).update(body).digest('hex')}`,
});

test('create a webhook, deliver signed JSON from outside, and start its trigger', async ({ page }) => {
  await page.goto('/');
  await api(page, 'post', '/api/runtime/definitions/', {
    name: 'E2E on webhook',
    objective: 'Summarize the delivery.',
    tool_ids: ['core'],
    kind: 'event',
    event_type: 'webhook.received',
  });

  await page.goto('/settings?tab=runtime');
  await page.getByLabel('Webhook name').fill('E2E sender');
  await page.getByRole('button', { name: 'Create webhook' }).click();
  const notice = page.getByTestId('webhook-secret');
  await expect(notice).toContainText('Copy these now');
  const url = (await notice.getByTestId('webhook-url').textContent()) ?? '';
  const path = new URL(url).pathname;
  const secret = (await notice.getByTestId('webhook-secret-value').textContent()) ?? '';
  expect(path).toMatch(/^\/api\/webhooks\/in\/[A-Za-z0-9_-]+$/);
  expect(secret).toMatch(/^whsec_/);

  // The sender has no session cookie.
  const sender = await playwrightRequest.newContext({ baseURL: E2E_BASE_URL });
  try {
    const body = JSON.stringify({ status: 'deployed', service: 'api' });
    const accepted = await sender.post(path, { data: body, headers: janusHeaders(secret, body) });
    expect(accepted.status(), await accepted.text()).toBe(202);
    expect(await accepted.json()).toEqual({ accepted: true, runs: 1 });

    const unsigned = await sender.post(path, { data: body, headers: { 'Content-Type': 'application/json' } });
    expect(unsigned.status()).toBe(401);

    const github = JSON.stringify({ ref: 'refs/heads/main' });
    expect((await sender.post(path, { data: github, headers: hubHeaders(secret, github) })).status()).toBe(202);
  } finally {
    await sender.dispose();
  }

  await page.getByRole('button', { name: 'Done' }).click();
  await expect(notice).toHaveCount(0);
  await page.reload();
  const row = page.getByTestId(/^webhook-\d+$/);
  await expect(row).toContainText('E2E sender');
  await expect(row).toContainText('2 deliveries');
  // The listing never shows the secret again.
  expect(await (await page.request.get('/api/webhooks')).text()).not.toContain(secret);

  await row.getByRole('button', { name: 'Delete E2E sender' }).click();
  await expect(page.getByText('No webhooks yet.')).toBeVisible();
});
