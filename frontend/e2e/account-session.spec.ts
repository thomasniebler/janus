import { expect, test } from './fixtures';
import { createThrowawayUser } from './helpers';

/**
 * Auth/session answers the API client validates (JAN-142): the signed-in user
 * (`/api/auth/me`), the saved shell preference, and the password-change acknowledgement.
 */
test.describe('Account and session', () => {
  // The shell preference is the user's own: a throwaway user's choice never reaches the shared
  // e2e admin, so nothing needs resetting (JAN-336).
  test.use({ asThrowawayUser: true });

  test('switches the app shell, keeps it across a reload, and switches back', async ({ page }) => {
    await page.goto('/settings?tab=appearance');
    const classic = page.getByRole('radio', { name: /Classic \(UIv1\)/ });
    const terminal = page.getByRole('radio', { name: /Terminal \(UIv2\)/ });
    await expect(classic).toHaveAttribute('aria-checked', 'true');

    const saved = page.waitForResponse(
      (response) => response.url().endsWith('/api/auth/me/ui-preference') && response.request().method() === 'PATCH'
    );
    await terminal.click();
    expect((await saved).ok()).toBeTruthy();
    await expect(terminal).toHaveAttribute('aria-checked', 'true');
    await expect(page.locator('html')).toHaveClass(/\bui-v2\b/);
    await expect(page.getByText('Could not save your appearance preference.', { exact: false })).toHaveCount(0);

    // The server's /api/auth/me answer, not only the local cache, carries the choice.
    await page.evaluate(() => window.localStorage.clear());
    await page.reload();
    await expect(page.getByRole('radio', { name: /Terminal \(UIv2\)/ })).toHaveAttribute('aria-checked', 'true');
    await expect(page.locator('html')).toHaveClass(/\bui-v2\b/);

    await page.getByRole('radio', { name: /Classic \(UIv1\)/ }).click();
    await expect(page.getByRole('radio', { name: /Classic \(UIv1\)/ })).toHaveAttribute('aria-checked', 'true');
    await expect(page.locator('html')).not.toHaveClass(/\bui-v2\b/);
  });

  test('changes the password of a separate account and signs in with the new one', async ({ browser, adminRequest }) => {
    // A throwaway user: changing the password revokes that user's sessions, which must
    // never be the shared e2e admin's.
    const { username, password: oldPassword } = await createThrowawayUser(adminRequest, 'e2e-password');
    const newPassword = 'e2e-new-password-5678!';

    const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const userPage = await context.newPage();
    const validationFailures: string[] = [];
    userPage.on('console', (message) => {
      if (message.type() === 'error' && message.text().startsWith('[api] Response validation failed')) {
        validationFailures.push(message.text());
      }
    });

    const signIn = async (password: string) => {
      await userPage.goto('/login');
      await userPage.getByLabel('Username').fill(username);
      await userPage.getByLabel('Password').fill(password);
      await userPage.getByRole('button', { name: 'Sign in', exact: true }).click();
    };

    await signIn(oldPassword);
    await expect(userPage).toHaveURL(/\/$/);

    await userPage.goto('/settings?tab=security');
    await userPage.getByLabel('Current Password').fill(oldPassword);
    await userPage.getByLabel('New Password', { exact: true }).fill(newPassword);
    await userPage.getByLabel('Repeat New Password').fill(newPassword);
    await userPage.getByRole('button', { name: 'Update Password' }).click();

    await expect(userPage).toHaveURL(/\/login\?changed=password/);
    await expect(userPage.getByText('Password updated. Sign in again with your new password.')).toBeVisible();

    await signIn(newPassword);
    await expect(userPage).toHaveURL(/\/$/);
    expect(validationFailures).toEqual([]);
    await context.close();
  });
});
