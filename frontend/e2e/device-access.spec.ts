import type { Page } from '@playwright/test';
import { E2E_BASE_URL } from './env';
import { expect, test } from './fixtures';

/**
 * Device tokens for the action inbox (Settings -> Security -> Desktop and device access): a token is
 * shown once, opens only the inbox, survives a reload as a prefix, and stops working once revoked. Each test
 * runs as its own throwaway user (the `asThrowawayUser` fixture).
 */
const openDeviceAccess = async (page: Page) => {
  await page.goto('/settings?tab=security');
  await expect(page.getByRole('heading', { name: 'Desktop and device access' })).toBeVisible();
};

test.describe('Device access', () => {
  test.use({ asThrowawayUser: true });

  test('creates a token that opens only the inbox, shows it once, and stops working when revoked', async ({
    page,
    playwright,
  }) => {
    // A client with no cookies: the config's storage state (the admin session) is also applied to new contexts unless
    // overridden, and a cookie wins over a bearer token.
    const device = await playwright.request.newContext({
      baseURL: E2E_BASE_URL,
      storageState: { cookies: [], origins: [] },
    });
    expect((await device.get('/api/inbox')).status()).toBe(401);
    await openDeviceAccess(page);
    await expect(page.getByText('No device tokens yet.')).toBeVisible();

    await page.getByRole('textbox', { name: 'Device name' }).fill('Laptop popup');
    await page.getByRole('button', { name: 'Create token' }).click();
    const secretField = page.getByRole('textbox', { name: 'New device token' });
    await expect(secretField).toBeVisible();
    const secret = await secretField.inputValue();
    expect(secret).toMatch(/^jdt_/);
    const headers = { Authorization: `Bearer ${secret}` };

    const inbox = await device.get('/api/inbox', { headers });
    expect(inbox.status()).toBe(200);
    expect(await inbox.json()).toEqual({ items: [] });
    // The token is refused everywhere but the inbox.
    expect((await device.get('/api/emails/notifications/preferences', { headers })).status()).toBe(401);
    expect((await device.post('/api/inbox/tokens', { headers, data: { name: 'escalate' } })).status()).toBe(401);

    // After a reload the secret is gone for good; only the name and a prefix remain.
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Desktop and device access' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'New device token' })).toHaveCount(0);
    const row = page
      .getByRole('list', { name: 'Device tokens' })
      .getByRole('listitem')
      .filter({ hasText: 'Laptop popup' });
    await expect(row).toContainText(secret.slice(0, 8));
    await expect(row).not.toContainText(secret);

    await row.getByRole('button', { name: 'Revoke Laptop popup' }).click();
    await expect(page.getByText('No device tokens yet.')).toBeVisible();
    expect((await device.get('/api/inbox', { headers })).status()).toBe(401);
    await device.dispose();
  });
});
