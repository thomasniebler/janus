import { type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { startFreshBackend, type FreshBackend } from './freshBackend';

// By the time this spec runs, global-setup has already bootstrapped an admin user
// directly via the `janus user bootstrap-admin` CLI (see ../e2e/global-setup.ts), and
// the e2e backend never sets `JANUS_PUBLIC_SETUP_ENABLED` (see ../e2e/env.ts), so the
// public one-time-token setup flow is disabled by default (`public_setup_enabled`
// defaults to `false` in `src/janus/config.py`). There is no way to exercise the fresh,
// user-less first-run setup form against this backend, so the first describe verifies
// what the app does with setup disabled: `GET /api/auth/setup` 404s, and `AppRouter` (see `src/App.tsx`)
// treats that the same as "setup not needed" and redirects `/setup` to `/login`. The
// real first-run flow runs further down against a second, user-less backend.
test.describe('Setup page (public setup disabled)', () => {
  test('visiting /setup redirects to the login page', async ({ page }) => {
    await page.goto('/setup');

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByLabel('Username')).toBeVisible();
    await expect(page.getByLabel('Password')).toBeVisible();
  });

  test('the setup status endpoint reports public setup is disabled', async ({ request }) => {
    const response = await request.get('/api/auth/setup');

    expect(response.status()).toBe(404);
    const body = await response.json();
    expect(body.detail).toBe('Initial setup is not available.');
  });
});

// The e2e backend already has an admin and public setup disabled (see above), so this
// scenario stubs `GET /api/auth/setup` to report a fresh, user-less install. That is
// enough to exercise the form's client-side 72-byte guard; the POST is intercepted to
// prove the over-limit password never leaves the browser.
test.describe('Setup page (fresh install, stubbed setup status)', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('rejects a password over the 72-byte bcrypt limit before submitting', async ({ page }) => {
    let setupPosts = 0;
    await page.route('**/api/auth/setup', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ needs_setup: true }) });
      }
      setupPosts += 1;
      return route.abort();
    });

    await page.goto('/setup');
    await expect(page.getByRole('heading', { name: 'Set up Janus' })).toBeVisible();

    const tooLong = 'é'.repeat(37); // 74 UTF-8 bytes
    await page.getByLabel('Setup Token').fill('any-token');
    await page.getByLabel('Username').fill('owner');
    await page.getByLabel('Password', { exact: true }).fill(tooLong);
    await page.getByLabel('Confirm Password').fill(tooLong);
    await page.getByRole('button', { name: 'Create Admin Account' }).click();

    await expect(page.getByText('Password must be at most 72 bytes long.')).toBeVisible();
    await expect(page).toHaveURL(/\/setup$/);
    expect(setupPosts).toBe(0);
  });
});

// The real first-run flow (JAN-218): a second backend with no users and public setup on
// prints a one-time token at startup; the operator pastes it into /setup, creates the
// first admin, signs in and lands on the dashboard. Nothing is stubbed here. The tests
// share that backend and run in order: the edge cases first, while setup is still open.
test.describe.serial('First-run setup (fresh install, public setup enabled)', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  const ownerUsername = 'first-owner';
  const ownerPassword = 'first-owner-password-1234!';
  let backend: FreshBackend;

  test.beforeAll(async () => {
    test.setTimeout(150_000);
    backend = await startFreshBackend('setup');
  });

  test.afterAll(async () => {
    await backend?.stop();
  });

  const fillSetupForm = async (page: Page, token: string, password: string, confirm = password) => {
    await page.getByLabel('Setup Token').fill(token);
    await page.getByLabel('Username').fill(ownerUsername);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByLabel('Confirm Password').fill(confirm);
    await page.getByRole('button', { name: 'Create Admin Account' }).click();
  };

  test('sends every route to /setup while no admin exists', async ({ page, request }) => {
    const status = await request.get(`${backend.baseURL}/api/auth/setup`);
    expect(await status.json()).toEqual({ needs_setup: true });

    for (const route of ['/', '/settings', '/login']) {
      await page.goto(`${backend.baseURL}${route}`);
      await expect(page).toHaveURL(`${backend.baseURL}/setup`);
      await expect(page.getByRole('heading', { name: 'Set up Janus' })).toBeVisible();
    }
  });

  test('refuses mismatched passwords without contacting the backend', async ({ page }) => {
    let setupPosts = 0;
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().endsWith('/api/auth/setup')) setupPosts += 1;
    });

    await page.goto(`${backend.baseURL}/setup`);
    await fillSetupForm(page, backend.setupToken, ownerPassword, `${ownerPassword}-typo`);

    await expect(page.getByText('Passwords do not match.')).toBeVisible();
    await expect(page).toHaveURL(`${backend.baseURL}/setup`);
    expect(setupPosts).toBe(0);
  });

  test('shows why a wrong setup token is refused and creates no account', async ({ page, request }) => {
    await page.goto(`${backend.baseURL}/setup`);
    await fillSetupForm(page, 'not-the-printed-token', ownerPassword);

    await expect(page.getByText('Setup token is invalid or has expired.')).toBeVisible();
    await expect(page).toHaveURL(`${backend.baseURL}/setup`);
    const status = await request.get(`${backend.baseURL}/api/auth/setup`);
    expect(await status.json()).toEqual({ needs_setup: true });
  });

  test('creates the first admin with the printed token, then signs in to the dashboard', async ({ page, request }) => {
    await page.goto(`${backend.baseURL}/setup`);
    await fillSetupForm(page, backend.setupToken, ownerPassword);

    await expect(page).toHaveURL(`${backend.baseURL}/login`);
    await page.getByLabel('Username').fill(ownerUsername);
    await page.getByLabel('Password').fill(ownerPassword);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();

    await expect(page).toHaveURL(`${backend.baseURL}/`);
    await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
    const me = await page.request.get(`${backend.baseURL}/api/auth/me`);
    expect(me.ok()).toBeTruthy();
    expect(await me.json()).toMatchObject({ username: ownerUsername, is_admin: true });

    // The earlier wrong-token attempt reached the audit log (it used to be lost to a
    // "database is locked" error after a 5 s stall).
    const events = await page.request.get(`${backend.baseURL}/api/events?source=Auth`);
    const messages = ((await events.json()).items as { message: string }[]).map((event) => event.message);
    expect(messages).toContain('Setup attempt failed: Setup token is invalid or has expired.');

    // Setup is closed for good: the status flips, the page is gone, the token is spent.
    const status = await request.get(`${backend.baseURL}/api/auth/setup`);
    expect(await status.json()).toEqual({ needs_setup: false });
    const replay = await request.post(`${backend.baseURL}/api/auth/setup`, {
      data: { token: backend.setupToken, username: 'second-owner', password: ownerPassword },
    });
    expect(replay.status()).toBe(404);
    expect((await replay.json()).detail).toBe('Initial setup has already been completed.');
  });

  test('/setup redirects to the login page once the admin exists', async ({ page }) => {
    await page.goto(`${backend.baseURL}/setup`);

    await expect(page).toHaveURL(`${backend.baseURL}/login`);
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  });
});
