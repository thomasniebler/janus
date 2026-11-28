import { expect, test } from './fixtures';

/**
 * Settings → Connections → Mail against the real backend (JAN-142): every answer here
 * (account list, saved account, connection test) is validated by the API client, so a
 * regression in either the backend shape or the client schema breaks this flow.
 * The account points at a closed local port, so nothing ever reaches a mail server.
 */
test.describe('Email accounts', () => {
  // The mailbox belongs to a throwaway user, so a failure never leaves it on the shared admin (JAN-336).
  test.use({ asThrowawayUser: true });

  test('adds, edits, tests and removes an IMAP account', async ({ page }) => {
    const name = `E2E Work Mail ${Date.now()}`;
    const renamed = `${name} (renamed)`;
    const address = `e2e-work-${Date.now()}@example.com`;

    await page.goto('/settings?tab=connections&sub=mail');
    await expect(page.getByRole('heading', { name: 'Connected Accounts' })).toBeVisible();

    await page.getByRole('button', { name: 'Custom IMAP' }).click();
    const addDialog = page.getByRole('dialog', { name: 'Add a custom IMAP account' });
    await addDialog.getByLabel('Account Name').fill(name);
    await addDialog.getByLabel('Email Address').fill(address);
    await addDialog.getByLabel('IMAP Server').fill('127.0.0.1');
    await addDialog.getByLabel('Port').fill('1');
    await addDialog.getByLabel('Username').fill('e2e-work');
    await addDialog.getByLabel('Password / App Password').fill('not-a-real-password');
    await addDialog.getByRole('button', { name: 'Save Account' }).click();
    await expect(addDialog).toHaveCount(0);

    // The innermost card holding the heading (ancestors come first in document order).
    const accountCard = (title: string) =>
      page.locator('div.rounded-lg.border').filter({ has: page.getByRole('heading', { name: title, exact: true }) }).last();
    const card = accountCard(name);
    await expect(card).toBeVisible();
    await expect(card).toContainText(address);

    // The connection test answers with a status and message; the closed port fails it.
    await card.getByRole('button', { name: 'Test Connection' }).click();
    await expect(card.getByText(`IMAP connection failed for ${address}`)).toBeVisible({ timeout: 15_000 });

    await card.getByRole('button', { name: 'Edit' }).click();
    const editDialog = page.getByRole('dialog', { name: 'Edit Email Account' });
    await expect(editDialog.getByLabel('Account Name')).toHaveValue(name);
    await editDialog.getByLabel('Account Name').fill(renamed);
    await editDialog.getByRole('button', { name: 'Save Changes' }).click();
    await expect(editDialog).toHaveCount(0);

    const renamedCard = accountCard(renamed);
    await expect(renamedCard).toBeVisible();

    // The saved name comes back from the server after a reload, not from local state.
    await page.reload();
    await expect(renamedCard).toBeVisible();

    // The delete button is the card's last (icon-only) button.
    await renamedCard.getByRole('button').last().click();
    await expect(page.getByRole('heading', { name: renamed, exact: true })).toHaveCount(0);
  });
});
