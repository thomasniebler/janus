import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';

// The e2e backend has no Microsoft app registration (env.ts), so no real sign-in can happen.
// The Microsoft connection is seeded in the database instead, as a finished "Connect Microsoft"
// would leave it, and only the overview's `configured` flag is overridden so the button is on.
const SEED_CONNECTION = `
from janus.services import microsoft_connect_service, oauth_connection_service
oauth_connection_service.upsert_connection(
    db, user.id, "microsoft", account_email=args[0], credentials="{}", scopes=[microsoft_connect_service.MAIL_SCOPE]
)
db.commit()
`;

const CLEAN_UP = `
db.query(models.EmailAccount).filter(models.EmailAccount.user_id == user.id, models.EmailAccount.auth_type == "microsoft").delete()
db.query(models.OAuthConnection).filter(models.OAuthConnection.user_id == user.id, models.OAuthConnection.provider == "microsoft").delete()
db.commit()
`;

test.describe('Outlook mailbox', () => {
  const address = `e2e-outlook-${Date.now()}@example.com`;

  test.afterEach(async () => {
    await runBackendPython(CLEAN_UP);
  });

  test('adds an Outlook mailbox on the connected Microsoft account and shows it as read over Graph', async ({
    page,
  }) => {
    await runBackendPython(SEED_CONNECTION, [address]);
    await page.route('**/api/connections', async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ response, json: { ...body, microsoft: { ...body.microsoft, configured: true } } });
    });

    await page.goto('/settings');
    await page.getByRole('button', { name: 'Add Microsoft mailbox' }).click();

    // This spec's mailbox is the only account on the page (others clean up after themselves).
    await expect(page.getByRole('heading', { name: `Outlook (${address})` })).toBeVisible();
    await expect(page.getByText(address, { exact: true })).toBeVisible();
    await expect(page.getByText('microsoft', { exact: true })).toBeVisible();
    // OAuth mailboxes have no password, so there is nothing to edit.
    await expect(page.getByRole('button', { name: 'Edit' })).toHaveCount(0);

    // The connection test goes through Graph; without a real sign-in it reports why it failed.
    await page.getByRole('button', { name: 'Test Connection' }).click();
    await expect(page.getByText(/Outlook connection failed/)).toBeVisible();
  });
});
