import { type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { runBackendPython } from './backendSeed';
import { createThrowawayUser, signInAs, type E2EUser } from './helpers';

const ACCOUNT_NAME = 'E2E rules mailbox';
const NEWSLETTER_SENDER = 'news@rules-e2e.example';
const NEWSLETTER_SUBJECT = 'E2E weekly newsletter';

/**
 * Seeds an inactive mailbox (never monitored) with one mail. Mail only arrives over IMAP,
 * so it is written directly; everything after that runs against the real backend through the UI.
 */
const SEED_SCRIPT = `
from datetime import datetime, UTC

account = models.EmailAccount(
    user_id=user.id, name="${ACCOUNT_NAME}", email="inbox@rules-e2e.example",
    imap_server="127.0.0.1", imap_port=1, username="inbox", password="unused", is_active=False,
)
db.add(account)
db.flush()

def mail(uid, sender, subject, priority):
    record = models.EmailRecord(
        account_id=account.id, message_id=f"<{uid}-{account.id}@rules-e2e.example>", imap_uid=uid, sender=sender,
        subject=subject, snippet=f"{subject} body", priority=priority, received_at=datetime.now(UTC),
    )
    db.add(record)
    db.flush()
    return record

mail(1, "${NEWSLETTER_SENDER}", "${NEWSLETTER_SUBJECT}", models.Priority.IMPORTANT)
db.commit()
`;

const openRulesTab = async (page: Page) => {
  await page.goto('/memory?tab=rules');
  await expect(page.getByRole('heading', { name: 'Prioritization Rules' })).toBeVisible();
};

/** The innermost card holding this heading (ancestors come first in document order). */
const cardWithHeading = (page: Page, title: string) =>
  page.locator('div.rounded-lg.border').filter({ has: page.getByRole('heading', { name: title, exact: true }) }).last();

// The mailbox, its mail, and every rule belong to one throwaway user that the tests share
// (JAN-336), never the shared e2e admin.
test.describe('Mail prioritization rules (JAN-142)', () => {
  let owner: E2EUser;

  test.beforeAll(async ({ adminRequest }) => {
    owner = await createThrowawayUser(adminRequest, 'e2e-mail-rules');
    await runBackendPython(SEED_SCRIPT, [], { as: owner.username });
  });

  test.beforeEach(async ({ page }) => {
    await signInAs(page, owner);
  });

  test('adds, reorders and deletes rules', async ({ page }) => {
    await openRulesTab(page);

    for (const [name, pattern] of [
      ['E2E rule first', 'first@rules-e2e.example'],
      ['E2E rule second', 'second@rules-e2e.example'],
    ]) {
      await page.getByRole('button', { name: 'New Rule' }).click();
      const dialog = page.getByRole('dialog', { name: 'Add New Rule' });
      await dialog.getByLabel('Rule Name').fill(name);
      await dialog.getByLabel('Priority').selectOption('digest');
      await dialog.getByLabel('Pattern (Regex or Keyword)').fill(pattern);
      await dialog.getByRole('button', { name: 'Add Rule' }).click();
      await expect(dialog).toHaveCount(0);
      await expect(cardWithHeading(page, name)).toContainText(`"${pattern}"`);
    }

    const ruleHeadings = page.getByRole('heading', { level: 3 }).filter({ hasText: /^E2E rule / });
    await expect(ruleHeadings).toHaveText(['E2E rule first', 'E2E rule second']);

    // Moving a rule sends one PATCH per swapped rule; each answer is a validated rule.
    const reorderStatuses: number[] = [];
    page.on('response', (response) => {
      if (/\/api\/emails\/rules\/\d+$/.test(response.url()) && response.request().method() === 'PATCH') {
        reorderStatuses.push(response.status());
      }
    });
    await cardWithHeading(page, 'E2E rule second').getByRole('button', { name: 'Move rule up' }).click();
    await expect.poll(() => reorderStatuses).toEqual([200, 200]);
    await page.reload();
    await expect(ruleHeadings).toHaveCount(2);

    for (const name of ['E2E rule first', 'E2E rule second']) {
      await cardWithHeading(page, name).getByRole('button').last().click();
      await expect(page.getByRole('heading', { name, exact: true })).toHaveCount(0);
    }
  });

  test('reclassifying a mail saves a rule for its sender', async ({ page }) => {
    await page.goto('/emails');
    const item = page.locator('[data-ui-item^="email:"]').filter({ hasText: NEWSLETTER_SUBJECT });
    await expect(item).toBeVisible();
    await expect(item).toContainText('important');

    const reclassified = page.waitForResponse(
      (response) => /\/api\/emails\/\d+\/priority/.test(response.url()) && response.request().method() === 'PATCH'
    );
    await item.getByRole('button', { name: 'Digest', exact: true }).click();
    expect((await reclassified).ok()).toBeTruthy();
    await expect(item.getByText('digest', { exact: true })).toBeVisible();

    await openRulesTab(page);
    await expect(cardWithHeading(page, `Messages from ${NEWSLETTER_SENDER}`)).toContainText('digest');
  });
});
