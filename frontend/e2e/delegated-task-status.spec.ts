import type { Page } from '@playwright/test';
import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import { api, signInAsNewUser } from './helpers';

/*
 * Delegated task status (JAN-224, on the JAN-208 panel). Unlike
 * `chat-delegated-tasks.spec.ts`, which replays the endpoint, these rows are written by the
 * real `delegation_service` (create, claim, complete/fail) and read back through the real
 * `/api/conversations/{id}/delegations` endpoint, so the panel shows exactly what the
 * backend stores. Two rows outlived their lease (JAN-207): one was interrupted once and is
 * queued again for its one automatic re-run; the other was interrupted again after that
 * re-run and has expired as failed.
 */

const SEED_DELEGATIONS = `
from datetime import UTC, datetime, timedelta
from janus import delegation_service as ds

conversation_id, stamp = int(args[0]), args[1]
parent = f"e2e-parent-{stamp}"

def delegate(objective, requested, allowed, **kwargs):
    return ds.create_delegation(
        db, owner_id=user.id, parent_task_id=parent, objective=objective,
        requested_tool_ids=requested, allowed_tool_ids=allowed, conversation_id=conversation_id, **kwargs,
    )

now = datetime.now(UTC)
# Interrupted once (requeued three hours ago), and its re-run never finished either.
expired = delegate(f"Reconcile last month's receipts {stamp}", ["emails"], ["emails"], now=now - timedelta(hours=6))
ds.expire_stale_delegations(db, user.id, now=now - timedelta(hours=3))
# Created long ago and never finished: the next sweep requeues it for its one re-run.
requeued = delegate(f"Tag last year's tax receipts {stamp}", ["documents"], ["documents"], now=now - timedelta(hours=6))
# From here on expiry runs at the real time: the first fails, the second is queued again.
completed = delegate(f"Collect this week's invoices {stamp}", ["emails"], ["emails", "planner"])
ds.complete_delegation(db, completed.id, ds.claim_delegation(db, completed.id),
                       "Found 3 invoices: Acme (due Fri), Globex, Initech.")
failed = delegate(f"Summarize the vendor contract {stamp}", ["documents"], ["documents"])
ds.fail_delegation(db, failed.id, ds.claim_delegation(db, failed.id), "the document search timed out")
running = delegate(f"Draft the quarterly numbers {stamp}", ["planner"], ["planner"])
ds.claim_delegation(db, running.id)
print(running.id)
`;

/** Finishes the running delegation the way its executor would, with the claim token it holds. */
const COMPLETE_RUNNING = `
record = db.get(models.RuntimeDelegation, int(args[0]))
from janus import delegation_service as ds
ds.complete_delegation(db, record.id, record.claim_token, "Revenue is up 4% on last quarter.")
`;

const chatSidebar = (page: Page) => page.getByRole('complementary', { name: 'Chat with Janus' });

/** A conversation in the sidebar's list (its accessible name also carries the nested delete button's). */
const conversationEntry = (page: Page, title: string) =>
  chatSidebar(page).locator('[role="button"]').filter({ hasText: title });

const openConversation = async (page: Page, title: string) => {
  await page.goto('/');
  await conversationEntry(page, title).click();
  await expect(conversationEntry(page, title)).toHaveAttribute('aria-current', 'true');
  return chatSidebar(page);
};

// Every test runs as its own throwaway user (JAN-336): the seed's lease sweep covers all of a
// user's delegations and the sidebar lists all of a user's conversations, so neither may
// include what other specs left on the shared e2e admin.
test.describe('Delegated task status from the backend (JAN-224)', () => {
  test.use({ asThrowawayUser: true });
  let stamp: string;
  let conversationId: number;
  let runningId: number;
  let username: string;
  const title = () => `E2E delegations ${stamp}`;

  test.beforeEach(async ({ page, account }) => {
    stamp = `${Date.now()}`;
    username = account.username;
    conversationId = (await (await api(page, 'post', '/api/conversations/', { title: title() })).json()).id;
    runningId = Number((await runBackendPython(SEED_DELEGATIONS, [conversationId, stamp], { as: username })).trim());
  });

  test('lists completed, failed, expired, requeued and running delegations, and picks up a finish', async ({
    page,
  }) => {
    const sidebar = await openConversation(page, title());
    const panel = sidebar.getByTestId('delegated-tasks');
    await expect(panel.locator('summary')).toContainText('Delegated tasks (5)');
    await expect(panel.locator('summary')).toContainText('2 active');
    await panel.locator('summary').click();

    const items = panel.getByRole('listitem');
    await expect(items).toHaveCount(5);
    // Newest first.
    await expect(items.nth(0)).toContainText(`Draft the quarterly numbers ${stamp}`);
    await expect(items.nth(3)).toContainText(`Tag last year's tax receipts ${stamp}`);
    await expect(items.nth(4)).toContainText(`Reconcile last month's receipts ${stamp}`);

    const completed = panel.getByRole('listitem', { name: 'Delegated task: Completed' });
    await expect(completed).toHaveCount(1);
    await expect(completed).toContainText(`Collect this week's invoices ${stamp}`);
    await expect(completed).toContainText('Found 3 invoices: Acme (due Fri), Globex, Initech.');
    // The tools the child was granted. The service refuses a request outside the grant,
    // so a stored row never has a withheld tool.
    await expect(completed.locator('code')).toHaveText(['emails', 'planner']);
    await expect(completed).not.toContainText('withheld');

    const failed = panel.getByRole('listitem', { name: 'Delegated task: Failed' });
    await expect(failed).toHaveCount(2);
    const contractFailure = failed.filter({ hasText: `Summarize the vendor contract ${stamp}` });
    await expect(contractFailure).toContainText('the document search timed out');
    // Expired: interrupted again after its one automatic re-run, so it failed for good.
    const expired = failed.filter({ hasText: `Reconcile last month's receipts ${stamp}` });
    await expect(expired).toContainText('delegation was interrupted again after its one automatic re-run');
    await expect(expired).not.toContainText('withheld');

    // Interrupted once: back in the queue for its re-run, active, with no error shown.
    const requeued = panel.getByRole('listitem', { name: 'Delegated task: Queued' });
    await expect(requeued).toHaveCount(1);
    await expect(requeued).toContainText(`Tag last year's tax receipts ${stamp}`);
    await expect(requeued.locator('p.bg-red-50')).toHaveCount(0);

    const running = panel.getByRole('listitem', { name: 'Delegated task: Running' });
    await expect(running).toContainText(`Draft the quarterly numbers ${stamp}`);
    await expect(running.locator('p.bg-red-50')).toHaveCount(0);

    // The panel keeps polling while a task is active, and shows the finish without a reload.
    await runBackendPython(COMPLETE_RUNNING, [runningId], { as: username });
    const finished = panel
      .getByRole('listitem', { name: 'Delegated task: Completed' })
      .filter({ hasText: `Draft the quarterly numbers ${stamp}` });
    await expect(finished).toContainText('Revenue is up 4% on last quarter.', { timeout: 15_000 });
    await expect(panel.getByRole('listitem', { name: 'Delegated task: Running' })).toHaveCount(0);
    // Only the requeued task is still active.
    await expect(panel.locator('summary')).toContainText('1 active');

    // The stored statuses the panel shows, as the endpoint returns them.
    const page1 = await (await page.request.get(`/api/conversations/${conversationId}/delegations?limit=10`)).json();
    expect(page1.total).toBe(5);
    expect(page1.items.map((item: { status: string }) => item.status)).toEqual([
      'completed',
      'failed',
      'completed',
      'queued',
      'failed',
    ]);
  });

  test("another user cannot see this conversation's delegated tasks", async ({ browser, adminRequest }) => {
    const { context, page: other } = await signInAsNewUser(browser, adminRequest, 'e2e-deleg');
    try {
      await other.goto('/');
      const response = await other.request.get(`/api/conversations/${conversationId}/delegations`);
      expect(response.status()).toBe(404);
      await expect(chatSidebar(other).getByText('No conversations yet.')).toBeVisible();
      await expect(conversationEntry(other, title())).toHaveCount(0);
    } finally {
      await context.close();
    }
  });

  test('a conversation without delegated tasks shows no panel', async ({ page }) => {
    const emptyTitle = `E2E no delegations ${stamp}`;
    const empty = (await (await api(page, 'post', '/api/conversations/', { title: emptyTitle })).json()).id;
    await page.goto('/');
    const listed = page.waitForResponse((response) => response.url().includes(`/api/conversations/${empty}/delegations`));
    await conversationEntry(page, emptyTitle).click();
    expect((await (await listed).json()).total).toBe(0);
    const sidebar = chatSidebar(page);
    await expect(sidebar.getByTestId('delegated-tasks')).toHaveCount(0);

    // Switching to the seeded conversation brings the panel back.
    await conversationEntry(page, title()).click();
    await expect(sidebar.getByTestId('delegated-tasks')).toContainText('Delegated tasks (5)');
  });
});
