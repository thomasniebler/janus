import { type BrowserContext, type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME } from './env';

// This whole file exercises the logged-out Login page, so it opts out of the
// project's default (already-authenticated) storage state.
test.use({ storageState: { cookies: [], origins: [] } });

const sessionCookie = async (context: BrowserContext) =>
  (await context.cookies()).find((cookie) => cookie.name === 'access_token');

// Signs in through the form as a fresh session of its own, so logging it out never
// touches the shared storage state the other specs use.
const signIn = async (page: Page) => {
  await page.goto('/login');
  await page.getByLabel('Username').fill(E2E_ADMIN_USERNAME);
  await page.getByLabel('Password').fill(E2E_ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
};

test.describe('Protected routes without a session', () => {
  for (const route of ['/', '/emails', '/settings', '/memory', '/chat']) {
    test(`${route} redirects to the login page`, async ({ page }) => {
      await page.goto(route);

      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
    });
  }

  test('the API refuses an anonymous caller', async ({ request }) => {
    expect((await request.get('/api/auth/me')).status()).toBe(401);
    expect((await request.get('/api/emails/')).status()).toBe(401);
  });
});

test.describe('Login page', () => {
  test('renders the username/password form and the Google sign-in option', async ({ page }) => {
    await page.goto('/login');

    await expect(page.getByLabel('Username')).toBeVisible();
    await expect(page.getByLabel('Password')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /sign in with google/i })).toBeVisible();
  });

  test('shows an error message for invalid credentials', async ({ page }) => {
    await page.goto('/login');

    await page.getByLabel('Username').fill('not-a-real-user');
    await page.getByLabel('Password').fill('wrong-password');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();

    await expect(page.getByText(/incorrect username or password/i)).toBeVisible();
  });

  test('a wrong password for a real user creates no session', async ({ page, context }) => {
    await page.goto('/login');

    await page.getByLabel('Username').fill(E2E_ADMIN_USERNAME);
    await page.getByLabel('Password').fill(`${E2E_ADMIN_PASSWORD}-wrong`);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();

    await expect(page.getByText(/incorrect username or password/i)).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
    expect(await sessionCookie(context)).toBeUndefined();
    expect((await page.request.get('/api/auth/me')).status()).toBe(401);

    // Nothing to fall back on: a protected page still bounces to the login form.
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('logs in and lands on the dashboard', async ({ page }) => {
    await page.goto('/login');

    await page.getByLabel('Username').fill(E2E_ADMIN_USERNAME);
    await page.getByLabel('Password').fill(E2E_ADMIN_PASSWORD);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();

    await expect(page).toHaveURL(/\/$/);
  });

  test('keeps the session in an HTTP-only cookie across a reload, then logs out', async ({ page, context, browser, baseURL }) => {
    await signIn(page);
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();

    const cookie = await sessionCookie(context);
    expect(cookie).toBeDefined();
    expect(cookie!.httpOnly).toBe(true);
    expect(cookie!.sameSite).toBe('Lax');
    // Scripts on the page cannot read the JWT.
    expect(await page.evaluate(() => document.cookie)).not.toContain('access_token');

    await page.reload();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();

    const logoutResponse = page.waitForResponse(
      (response) => response.url().endsWith('/api/auth/logout') && response.request().method() === 'POST'
    );
    await page.getByRole('button', { name: 'Logout' }).click();
    expect((await logoutResponse).ok()).toBeTruthy();

    await expect(page).toHaveURL(/\/login$/);
    expect(await sessionCookie(context)).toBeUndefined();
    await page.goto('/');
    await expect(page).toHaveURL(/\/login$/);

    // Logout revokes the token itself, so a copy of the old cookie is useless too.
    // A context from `browser.newContext()` does not inherit the config's `use`
    // options, so it needs the base URL to resolve the relative API path.
    const replay = await browser.newContext({ baseURL, storageState: { cookies: [cookie!], origins: [] } });
    try {
      expect((await replay.request.get('/api/auth/me')).status()).toBe(401);
    } finally {
      await replay.close();
    }
  });

  test('"Sign in with Google" starts the backend OAuth redirect', async ({ page }) => {
    await page.goto('/login');

    const googleRequest = page.waitForRequest((request) => request.url().includes('/api/auth/google'));
    await page.getByRole('button', { name: /sign in with google/i }).click();
    await googleRequest;
  });
});
