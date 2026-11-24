import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

/**
 * Secretary settings (Settings -> Daily Briefing): waking hours and the WhatsApp alert switch and
 * number, stored on the notification preferences. Each test runs as its own throwaway user, so the
 * shared e2e admin's preferences are never touched.
 */
const openSecretarySettings = async (page: Page) => {
  await page.goto('/settings?tab=briefing');
  await expect(page.getByRole('heading', { name: 'Secretary', exact: true })).toBeVisible();
};

const save = (page: Page) => page.getByRole('button', { name: 'Save Secretary Settings' }).click();

test.describe('Secretary settings', () => {
  test.use({ asThrowawayUser: true });

  test('shows the defaults, saves hours and a WhatsApp number, and keeps them after a reload', async ({ page }) => {
    await openSecretarySettings(page);
    await expect(page.getByRole('combobox', { name: 'Active from' })).toHaveValue('8');
    await expect(page.getByRole('combobox', { name: 'Active until' })).toHaveValue('21');
    await expect(page.getByRole('checkbox', { name: /whatsapp alerts/i })).not.toBeChecked();
    await expect(page.getByRole('textbox', { name: 'Your WhatsApp number' })).toHaveValue('');

    await page.getByRole('combobox', { name: 'Active from' }).selectOption('9');
    await page.getByRole('combobox', { name: 'Active until' }).selectOption('18');
    await page.getByRole('checkbox', { name: /whatsapp alerts/i }).check();
    // A number in the 00 form is stored as +digits.
    await page.getByRole('textbox', { name: 'Your WhatsApp number' }).fill('0049 151 1234-5678');
    await save(page);
    await expect(page.getByRole('status').filter({ hasText: 'Secretary settings saved.' })).toBeVisible();

    await page.reload();
    await expect(page.getByRole('heading', { name: 'Secretary', exact: true })).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Active from' })).toHaveValue('9');
    await expect(page.getByRole('combobox', { name: 'Active until' })).toHaveValue('18');
    await expect(page.getByRole('checkbox', { name: /whatsapp alerts/i })).toBeChecked();
    await expect(page.getByRole('textbox', { name: 'Your WhatsApp number' })).toHaveValue('+4915112345678');
  });

  test('shows the server message for a national number and for a window that ends before it starts', async ({
    page,
  }) => {
    await openSecretarySettings(page);

    await page.getByRole('textbox', { name: 'Your WhatsApp number' }).fill('015112345678');
    await save(page);
    await expect(page.getByRole('status').filter({ hasText: 'international number' })).toBeVisible();

    await page.getByRole('textbox', { name: 'Your WhatsApp number' }).fill('');
    await page.getByRole('combobox', { name: 'Active from' }).selectOption('20');
    await page.getByRole('combobox', { name: 'Active until' }).selectOption('9');
    await save(page);
    await expect(page.getByRole('status').filter({ hasText: 'must end after they start' })).toBeVisible();

    // Nothing was stored by the rejected saves.
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'Active from' })).toHaveValue('8');
    await expect(page.getByRole('textbox', { name: 'Your WhatsApp number' })).toHaveValue('');
  });
});
