import {
  expect,
  type APIRequestContext,
  type APIResponse,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from '@playwright/test';

/** Who a backend call is made as: a page (its context's session) or an API request context. */
export type Session = Page | APIRequestContext;

const requestOf = (session: Session): APIRequestContext => ('goto' in session ? session.request : session);

/**
 * Calls the backend as whoever `session` is signed in as (the shared e2e admin unless the
 * test signed in as someone else), with the CSRF header every state-changing route needs.
 * Fails the test on a non-2xx answer.
 */
export const api = async (
  session: Session,
  method: 'post' | 'patch' | 'put' | 'delete',
  url: string,
  data?: unknown,
): Promise<APIResponse> => {
  const request = requestOf(session);
  const { csrf_token: csrfToken } = await (await request.get('/api/auth/csrf')).json();
  const response = await request[method](url, { data, headers: { 'X-CSRF-Token': csrfToken } });
  expect(response.ok(), `${method.toUpperCase()} ${url} -> ${response.status()} ${await response.text()}`).toBeTruthy();
  return response;
};

/** The in-app ConfirmDialog (JAN-292) with this title; the browser's own dialog is never used. */
export const confirmDialog = (page: Page, title: string): Locator => page.getByRole('dialog', { name: title });

/** Waits for the ConfirmDialog, clicks one of its buttons, and waits for it to close. */
export const answerConfirm = async (page: Page, title: string, button: string) => {
  const dialog = confirmDialog(page, title);
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: button, exact: true }).click();
  await expect(dialog).toHaveCount(0);
};

/**
 * Points the utility model at a cloud provider so the Knowledge extraction and
 * Wiki synthesis switches show their cloud warning. Nothing calls the model:
 * the e2e backend runs with the trigger worker disabled. Undo with
 * `resetUtilityModel`.
 */
export const setCloudUtilityModel = (admin: Session) =>
  api(admin, 'patch', '/api/planner/settings', { utility_llm_provider: 'openai', utility_llm_model: 'gpt-4o-mini' });

export const resetUtilityModel = (admin: Session) =>
  api(admin, 'patch', '/api/planner/settings', { utility_llm_provider: null, utility_llm_model: null });

/**
 * Counts the page's `/ws/chat` sockets that reached OPEN. Call before navigating;
 * `waitForOpenChatSocket` then waits for one. A message sent while the socket is
 * still connecting is not sent (the composer keeps the draft), so a spec that talks
 * to the real backend waits for the socket first.
 */
export const trackChatSocketOpen = (page: Page) =>
  page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket;
    const counter = window as unknown as { __e2eOpenChatSockets: number };
    counter.__e2eOpenChatSockets = 0;
    window.WebSocket = class extends NativeWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        if (String(url).includes('/ws/chat')) {
          this.addEventListener('open', () => {
            counter.__e2eOpenChatSockets += 1;
          });
        }
      }
    };
  });

export const waitForOpenChatSocket = (page: Page) =>
  page.waitForFunction(() => ((window as unknown as { __e2eOpenChatSockets?: number }).__e2eOpenChatSockets ?? 0) > 0);

/** A promise plus the function that resolves it, for holding back a stubbed reply. */
export const deferred = () => {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
};

export interface E2EUser {
  username: string;
  password: string;
}

let throwawayUserCount = 0;

/**
 * Creates a non-admin user through the admin's session (`admin`: the admin's page before it
 * signs in as someone else, or the `adminRequest` fixture) and returns its credentials.
 *
 * Everything a test owns (memory facts, topics, widgets, mail, triggers, conversations) is
 * per user, so a test that runs as its own throwaway user never sees what other specs left
 * on the shared e2e admin, and never leaves anything there for them. A spec opts in with
 * `test.use({ asThrowawayUser: true })` (see `fixtures.ts`); this helper is for the rest,
 * such as a second user in the same test or one user shared by a describe block.
 */
export const createThrowawayUser = async (admin: Session, prefix = 'e2e-user'): Promise<E2EUser> => {
  throwawayUserCount += 1;
  const username = `${prefix}-${Date.now()}-${process.pid}-${throwawayUserCount}`;
  const password = 'e2e-throwaway-password-1234!';
  await api(admin, 'post', '/api/auth/users', { username, password });
  return { username, password };
};

/**
 * Signs `page`'s browser context out of whatever session it has and in as this user,
 * through the real login endpoint (the login form itself is covered by `login.spec.ts`).
 * The page does not navigate; go to the page under test next.
 */
export const signInAs = async (page: Page, { username, password }: E2EUser) => {
  await page.context().clearCookies();
  const response = await page.request.post('/api/auth/login', { data: { username, password } });
  expect(response.ok(), `sign-in as ${username} -> ${response.status()} ${await response.text()}`).toBeTruthy();
};

/**
 * Creates a throwaway user and signs it in in a fresh browser context next to the test's own
 * page, for a test that needs a second user at the same time. Close the context when done.
 */
export const signInAsNewUser = async (
  browser: Browser,
  admin: Session,
  prefix = 'e2e-member',
): Promise<{ context: BrowserContext; page: Page; user: E2EUser }> => {
  const user = await createThrowawayUser(admin, prefix);
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await signInAs(page, user);
  return { context, page, user };
};
