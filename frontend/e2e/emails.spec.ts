import { type Locator, type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { seedMail, type SeededMail } from './backendSeed';
import { signInAs } from './helpers';

// Every test here runs as a throwaway user, never the shared e2e admin, so what other specs
// leave on the admin never changes what these tests see, and the other way round.

test.describe('Emails page', () => {
  test.use({ asThrowawayUser: true });

  test('renders the empty state for a fresh account', async ({ page }) => {
    await page.goto('/emails');

    await expect(page.getByRole('heading', { name: 'Email Intelligence' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'No emails found' })).toBeVisible();
    await expect(
      page.getByText('No emails yet. Add an IMAP account in Settings or adjust your current filter.')
    ).toBeVisible();
  });

  test('switches between the priority filters', async ({ page }) => {
    await page.goto('/emails');

    const allButton = page.getByRole('button', { name: 'all', exact: true });
    const importantButton = page.getByRole('button', { name: 'important', exact: true });

    await expect(allButton).toHaveClass(/bg-primary/);
    await expect(importantButton).not.toHaveClass(/bg-primary/);

    await importantButton.click();

    await expect(importantButton).toHaveClass(/bg-primary/);
    await expect(allButton).not.toHaveClass(/bg-primary/);
    // Still no emails match this filter on a fresh account.
    await expect(page.getByRole('heading', { name: 'No emails found' })).toBeVisible();
  });

  test('shows a fallback banner when the selected email is no longer available', async ({ page }) => {
    await page.goto('/emails?email=999999');

    await expect(
      page.getByText('The selected email is no longer available in the current list.')
    ).toBeVisible();
  });
});

// The card around a heading: the nearest ancestor that also holds buttons.
const cardOf = (heading: Locator): Locator => heading.locator('xpath=ancestor::div[.//button][1]');

// Seeded mail against the real backend (JAN-219): the rows go in through the backend's
// own models and services (see `backendSeed.ts`), so no live IMAP server is involved. The mail
// belongs to a throwaway user the seed creates. The tests share the seed and run in
// order, because a re-classification changes what the later ones see.
test.describe.serial('Emails page with seeded mail', () => {
  let seeded: SeededMail;

  test.beforeAll(async ({ adminRequest }) => {
    seeded = await seedMail(adminRequest);
  });

  test.beforeEach(async ({ page }) => {
    await signInAs(page, seeded.user);
  });

  const item = (page: Page, key: keyof SeededMail['emails']) =>
    page.locator(`[data-ui-item="email:${seeded.emails[key]}"]`);
  const listedSubjects = (page: Page) => page.locator('[data-ui-item^="email:"] h3');
  const filterButton = (page: Page, name: string) => page.getByRole('button', { name, exact: true });
  // The detail card: the nearest ancestor of its subject that also holds the message content.
  const detailView = (page: Page, subject: string) =>
    page
      .getByRole('heading', { level: 2, name: subject })
      .locator('xpath=ancestor::div[.//h3[normalize-space()="Message Content"]][1]');

  test('lists the classified mail newest first, without spam', async ({ page }) => {
    await page.goto('/emails');

    await expect(listedSubjects(page)).toHaveText([
      'E2E Board meeting moved to Monday',
      'E2E Weekly newsletter',
      'E2E Lunch on Friday',
    ]);
    const board = item(page, 'board');
    await expect(board.getByText('ceo@e2e.example')).toBeVisible();
    await expect(board.getByText('important', { exact: true })).toBeVisible();
    await expect(board.getByText('high confidence')).toBeVisible();
    await expect(item(page, 'newsletter').getByText('medium confidence')).toBeVisible();
    await expect(item(page, 'prize')).toHaveCount(0);
  });

  test('filters by priority, including the spam folder', async ({ page }) => {
    await page.goto('/emails');
    await expect(listedSubjects(page)).toHaveCount(3);

    await filterButton(page, 'important').click();
    await expect(listedSubjects(page)).toHaveText(['E2E Board meeting moved to Monday']);

    await filterButton(page, 'digest').click();
    await expect(listedSubjects(page)).toHaveText(['E2E Weekly newsletter', 'E2E Lunch on Friday']);

    await filterButton(page, 'spam').click();
    await expect(listedSubjects(page)).toHaveText(['E2E You won a prize']);
    await expect(item(page, 'prize').getByText('low confidence')).toBeVisible();

    await filterButton(page, 'all').click();
    await expect(listedSubjects(page)).toHaveCount(3);
  });

  test('opens an email in the detail view and closes it again', async ({ page }) => {
    const subject = 'E2E Board meeting moved to Monday';
    await page.goto('/emails');

    await item(page, 'board').getByRole('heading', { name: subject }).click();

    await expect(page).toHaveURL(new RegExp(`/emails\\?email=${seeded.emails.board}$`));
    const detail = detailView(page, subject);
    await expect(detail.getByText('From:')).toBeVisible();
    await expect(detail.getByText('ceo@e2e.example')).toBeVisible();
    await expect(detail.getByText('The CEO asks you to prepare slides.')).toBeVisible();
    await expect(detail.getByText(`Body of ${subject}.`)).toBeVisible();

    // The open email is part of the URL, so it survives a reload.
    await page.reload();
    await expect(detail.getByText('From:')).toBeVisible();

    await detail.getByRole('button', { name: 'Close email' }).click();
    await expect(page).toHaveURL(/\/emails$/);
    await expect(page.getByRole('heading', { level: 2, name: subject })).toHaveCount(0);
  });

  test('re-classifies an email from its detail view and saves the correction as a rule', async ({ page }) => {
    await page.goto(`/emails?email=${seeded.emails.lunch}`);
    const detail = detailView(page, 'E2E Lunch on Friday');
    await expect(detail.getByText('From:')).toBeVisible();

    const correction = page.waitForResponse(
      (response) => response.url().includes(`/api/emails/${seeded.emails.lunch}/priority`) && response.ok()
    );
    await detail.getByRole('button', { name: 'Ignore', exact: true }).click();
    await correction;

    await expect(detail.getByText('ignore', { exact: true })).toBeVisible();
    await expect(detail.getByText('low confidence')).toBeVisible();
    await expect(
      detail.getByText(
        'Corrected by user from digest to ignore. Janus saved this correction as an active sender rule for similar messages.'
      )
    ).toBeVisible();
    await page.reload();
    await expect(item(page, 'lunch').getByText('ignore', { exact: true })).toBeVisible();

    // Since JAN-74 a manual correction becomes an active rule right away, shown under
    // Memory -> Rules, and queues no clarification item of its own.
    await page.goto('/memory?tab=rules');
    await expect(page.getByRole('heading', { name: 'Messages from colleague@e2e.example' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'E2E Lunch on Friday' })).toHaveCount(0);
  });

  test('re-classifies an email straight from the list; spam leaves the inbox', async ({ page }) => {
    await page.goto('/emails');
    const newsletter = item(page, 'newsletter');

    await newsletter.getByRole('button', { name: 'Spam', exact: true }).click();

    await expect(newsletter).toHaveCount(0);
    // Classifying from the list must not open the email.
    await expect(page).toHaveURL(/\/emails$/);
    await filterButton(page, 'spam').click();
    await expect(listedSubjects(page)).toHaveText(['E2E Weekly newsletter', 'E2E You won a prize']);
  });

  test('approves and dismisses clarification items', async ({ page }) => {
    await page.goto('/memory?tab=rules');
    await expect(page.getByRole('heading', { name: 'Clarify misunderstandings' })).toBeVisible();

    const boardCard = cardOf(page.getByRole('heading', { name: 'E2E Board meeting moved to Monday' }));
    const prizeCard = cardOf(page.getByRole('heading', { name: 'E2E You won a prize' }));
    await expect(boardCard.getByText('Messages from ceo@e2e.example')).toBeVisible();
    await expect(boardCard.getByText('high confidence')).toBeVisible();
    await expect(prizeCard.getByText('Messages from prize@e2e-spam.example')).toBeVisible();
    // Nothing is applied before the user decides.
    await expect(page.getByRole('heading', { name: 'Messages from ceo@e2e.example' })).toHaveCount(0);

    await boardCard.getByRole('button', { name: 'Approve rule' }).click();
    await expect(page.getByRole('heading', { name: 'E2E Board meeting moved to Monday' })).toHaveCount(0);
    const approvedRule = page.getByRole('heading', { name: 'Messages from ceo@e2e.example' });
    await expect(approvedRule).toBeVisible();
    await expect(cardOf(approvedRule).getByText('digest', { exact: true })).toBeVisible();

    await prizeCard.getByRole('button', { name: 'Dismiss' }).click();
    await expect(page.getByRole('heading', { name: 'E2E You won a prize' })).toHaveCount(0);
    await expect(page.getByText(/No pending clarifications/)).toBeVisible();

    await page.reload();
    await expect(page.getByText(/No pending clarifications/)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Messages from ceo@e2e.example' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Messages from prize@e2e-spam.example' })).toHaveCount(0);

    const clarifications: { id: number; status: string }[] = await (
      await page.request.get('/api/emails/clarifications')
    ).json();
    const statusById = Object.fromEntries(clarifications.map((c) => [c.id, c.status]));
    expect(statusById[seeded.clarifications.board]).toBe('approved');
    expect(statusById[seeded.clarifications.prize]).toBe('dismissed');
  });
});

test.describe('Rule management', () => {
  const ruleName = 'E2E invoices to spam';
  const pattern = '^E2E (Invoice|Receipt) \\d+$';

  test.use({ asThrowawayUser: true });

  test('creates a regex rule, keeps it across a reload, and deletes it', async ({ page }) => {
    await page.goto('/memory?tab=rules');
    await expect(page.getByRole('heading', { name: 'Prioritization Rules' })).toBeVisible();

    await page.getByRole('button', { name: 'New Rule' }).click();
    const modal = page.getByRole('dialog', { name: 'Add New Rule' });
    await modal.getByLabel('Rule Name').fill(ruleName);
    await modal.getByLabel('Field').selectOption('subject');
    await modal.getByLabel('Priority').selectOption('spam');
    await modal.getByLabel('Pattern (Regex or Keyword)').fill(pattern);
    await modal.getByRole('button', { name: 'Add Rule' }).click();

    await expect(modal).toHaveCount(0);
    const rule = page.getByRole('heading', { name: ruleName });
    await expect(rule).toBeVisible();
    await expect(cardOf(rule).getByText(`"${pattern}"`)).toBeVisible();

    await page.reload();
    await expect(page.getByRole('heading', { name: ruleName })).toBeVisible();
    const rules: { name: string }[] = await (await page.request.get('/api/emails/rules')).json();
    expect(rules.find((r) => r.name === ruleName)).toMatchObject({
      field: 'subject',
      priority: 'spam',
      pattern,
      is_active: true,
    });

    await page.getByRole('button', { name: `Delete rule ${ruleName}` }).click();
    await expect(page.getByRole('heading', { name: ruleName })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Prioritization Rules' })).toBeVisible();
    await expect(page.getByRole('heading', { name: ruleName })).toHaveCount(0);
  });

  test('refuses an invalid regex with a visible reason and saves nothing', async ({ page }) => {
    await page.goto('/memory?tab=rules');

    await page.getByRole('button', { name: 'New Rule' }).click();
    const modal = page.getByRole('dialog', { name: 'Add New Rule' });
    await modal.getByLabel('Rule Name').fill('E2E broken rule');
    await modal.getByLabel('Pattern (Regex or Keyword)').fill('(unclosed');
    await modal.getByRole('button', { name: 'Add Rule' }).click();

    await expect(modal.getByText('Invalid regex pattern')).toBeVisible();
    await expect(modal).toBeVisible();
    const rules: { name: string }[] = await (await page.request.get('/api/emails/rules')).json();
    expect(rules.map((r) => r.name)).not.toContain('E2E broken rule');
  });
});
