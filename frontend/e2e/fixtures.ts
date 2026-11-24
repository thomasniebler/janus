import { expect, test as base, type APIRequestContext } from '@playwright/test';
import { E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME, E2E_BASE_URL, E2E_STORAGE_STATE_PATH } from './env';
import { createThrowawayUser, signInAs, type E2EUser } from './helpers';

/** The console line `src/lib/apiValidation.ts` logs when a response fails its schema (JAN-142). */
const API_VALIDATION_LOG = '[api] Response validation failed';

interface TestFixtures {
  allowApiValidationErrors: boolean;
  apiValidationGuard: void;
  /** Run the test as its own throwaway user instead of the shared e2e admin (JAN-336). */
  asThrowawayUser: boolean;
  /** Who `page` is signed in as: the throwaway user, or the shared e2e admin. */
  account: E2EUser;
}

interface WorkerFixtures {
  /** The shared e2e admin's session, for admin-only and instance-wide calls. */
  adminRequest: APIRequestContext;
}

/**
 * Playwright's `test` with the suite's shared fixtures. Every spec imports `test` from here.
 *
 * - An automatic guard: any page in a spec that logs an API response validation failure
 *   fails that test, naming the endpoint and fields, so the whole suite checks the client's
 *   runtime schemas against the real backend's answers. A spec that serves a malformed
 *   response on purpose opts out with `test.use({ allowApiValidationErrors: true })`.
 * - `asThrowawayUser` (JAN-336): with `test.use({ asThrowawayUser: true })`, `page` is signed
 *   in as a fresh non-admin user before any `beforeEach` runs, so the test owns all of its
 *   per-user data (memory facts, topics, widgets, mail, triggers, conversations) and never
 *   depends on what other specs left on the shared admin. `account` names that user.
 * - `adminRequest`: an API client signed in as the shared e2e admin, for instance-wide settings
 *   (the backend model, the utility model) a throwaway user may not change. A test that
 *   changes one resets it in `afterEach`.
 */
export const test = base.extend<TestFixtures, WorkerFixtures>({
  adminRequest: [
    async ({ playwright }, use) => {
      const context = await playwright.request.newContext({ baseURL: E2E_BASE_URL, storageState: E2E_STORAGE_STATE_PATH });
      await use(context);
      await context.dispose();
    },
    { scope: 'worker' },
  ],
  asThrowawayUser: [false, { option: true }],
  account: [
    async ({ page, asThrowawayUser, adminRequest }, use) => {
      if (!asThrowawayUser) {
        await use({ username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD });
        return;
      }
      const user = await createThrowawayUser(adminRequest);
      await signInAs(page, user);
      await use(user);
    },
    { auto: true },
  ],
  allowApiValidationErrors: [false, { option: true }],
  apiValidationGuard: [
    async ({ page, allowApiValidationErrors }, use) => {
      const failures: string[] = [];
      page.on('console', (message) => {
        if (message.type() === 'error' && message.text().startsWith(API_VALIDATION_LOG)) {
          failures.push(message.text());
        }
      });
      await use();
      if (!allowApiValidationErrors) {
        expect(failures, 'API responses that failed their client-side schema').toEqual([]);
      }
    },
    { auto: true },
  ],
});

export { expect };
