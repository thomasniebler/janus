import type { Locator, Page } from '@playwright/test';
import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import { answerConfirm, api } from './helpers';

/*
 * Structured topics (JAN-224): a manual topic from Memory & Knowledge -> Topics shows up in
 * the dashboard's Topic Focus widget, and a topic Janus spotted in mail is only *watched*
 * (a candidate, kept off the dashboard) until a second, distinct email corroborates it or
 * the user confirms it. Mail only arrives over IMAP, so the mailbox and its mail are seeded,
 * and each mention goes through the real `TopicService.record_email_topic` that mail
 * processing calls, with its candidate/corroboration rules.
 */

const SEED_MAILBOX = `
from datetime import UTC, datetime, timedelta

stamp = args[0]
account = models.EmailAccount(
    user_id=user.id, name=f"E2E topics mailbox {stamp}", email=f"topics-{stamp}@topics-e2e.example",
    imap_server="127.0.0.1", imap_port=1, username="inbox", password="unused", is_active=False,
)
db.add(account)
db.flush()
for uid in (1, 2):
    db.add(models.EmailRecord(
        account_id=account.id, message_id=f"<{stamp}-{uid}@topics-e2e.example>", imap_uid=uid,
        sender="pm@topics-e2e.example", subject=f"Roof repair quote #{uid}", snippet=f"Roof repair, quote number {uid}",
        priority=models.Priority.IMPORTANT, received_at=datetime.now(UTC) - timedelta(minutes=3 - uid),
    ))
db.commit()
`;

/** Records that mail number \`args[2]\` mentions the topic titled \`args[1]\`, as mail processing would. */
const MENTION = `
from janus.services.topic_service import TopicService

stamp, title, uid = args[0], args[1], int(args[2])
email = db.query(models.EmailRecord).filter(models.EmailRecord.message_id == f"<{stamp}-{uid}@topics-e2e.example>").one()
topics = TopicService(db).record_email_topic(
    email, user_id=user.id, title=title, summary=f"Quotes and scheduling for {title}.", confidence="high",
)
db.commit()
print(" ".join(f"{topic.id}:{topic.status.value}" for topic in topics))
`;

/** One mention, recorded for `username`; returns the topic's id and status after it. */
const mention = async (username: string, stamp: string, title: string, uid: number) => {
  const [id, status] = (await runBackendPython(MENTION, [stamp, title, uid], { as: username })).trim().split(':');
  return { id: Number(id), status };
};

const topicFocus = (page: Page) => page.getByTestId('dashboard-widget-topic_focus');
const focusEntry = (page: Page, title: string): Locator =>
  topicFocus(page).getByRole('button').filter({ hasText: title });

/** The topic's card in the Tracked topics list. */
const topicCard = (page: Page, title: string): Locator =>
  page.locator('[data-ui-item^="topic:"]').filter({ has: page.getByText(title, { exact: true }) });

const openTopics = async (page: Page, status: 'Active' | 'Watching' | 'Resolved' | 'All' = 'Active') => {
  await page.goto('/memory?tab=topics');
  await expect(page.getByRole('heading', { name: 'Topics', exact: true })).toBeVisible();
  await page.getByLabel('Status').selectOption({ label: status });
};

// Every test runs as its own throwaway user (JAN-336): the Topic Focus widget shows a user's
// topics (pinned first, capped), so topics other specs leave on the shared admin, or a widget
// they hide there, must never decide what these tests see.
test.describe('Topics and the Topic Focus widget (JAN-224)', () => {
  test.use({ asThrowawayUser: true });
  let stamp: string;
  let username: string;

  test.beforeEach(async ({ account }) => {
    stamp = `${Date.now()}`;
    username = account.username;
    await runBackendPython(SEED_MAILBOX, [stamp], { as: username });
  });

  test('a manual topic shows on the dashboard; pin, resolve, reactivate and delete follow it there', async ({
    page,
  }) => {
    const title = `Home blog ${stamp}`;
    await page.goto('/memory?tab=topics');
    await page.getByLabel('Manual topic title').fill(title);
    await page.getByLabel('Manual topic description').fill('Posts and drafts for the home blog.');
    await page.getByLabel('Manual topic marker keywords').fill(`blog-${stamp}, drafts`);
    await page.getByRole('button', { name: /save topic/i }).click();
    // A manual topic is confirmed and active from the start.
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
    await expect(topicCard(page, title)).toContainText('active');
    await expect(topicCard(page, title)).not.toContainText('Watching for now');

    await page.goto('/');
    const entry = focusEntry(page, title);
    await expect(entry).toBeVisible();
    await expect(entry).toContainText('active');
    await expect(entry).toContainText('Posts and drafts for the home blog.');
    await expect(entry).toContainText('0 evidence items');

    // The widget links to the topic's detail.
    await entry.click();
    await expect(page).toHaveURL(new RegExp(`/memory\\?.*topic=\\d+`));
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    // Pinned topics lead the widget.
    await openTopics(page);
    await topicCard(page, title).getByRole('button', { name: 'Pin', exact: true }).click();
    await expect(topicCard(page, title)).toContainText('Pinned');
    await page.goto('/');
    await expect(focusEntry(page, title)).toContainText('Pinned');
    await expect(topicFocus(page).locator('[data-ui-item^="topic:"]').first()).toContainText(title);

    // Resolved topics leave the widget; reactivating brings them back.
    await openTopics(page);
    await topicCard(page, title).getByRole('button', { name: 'Resolve' }).click();
    await expect(topicCard(page, title)).toHaveCount(0);
    await page.goto('/');
    await expect(topicFocus(page)).toBeVisible();
    await expect(focusEntry(page, title)).toHaveCount(0);

    await openTopics(page, 'Resolved');
    await expect(topicCard(page, title)).toContainText('resolved');
    await topicCard(page, title).getByRole('button', { name: 'Reactivate' }).click();
    await expect(topicCard(page, title)).toHaveCount(0);
    await page.goto('/');
    await expect(focusEntry(page, title)).toBeVisible();

    await openTopics(page);
    await page.getByRole('button', { name: `Delete topic "${title}"` }).click();
    await answerConfirm(page, 'Delete topic?', 'Delete');
    await expect(topicCard(page, title)).toHaveCount(0);
    await page.goto('/');
    await expect(topicFocus(page)).toBeVisible();
    await expect(focusEntry(page, title)).toHaveCount(0);
  });

  test('a topic spotted in one email is watched, and a second email promotes it onto the dashboard', async ({
    page,
  }) => {
    const title = `Roof repair ${stamp}`;
    const first = await mention(username, stamp, title, 1);
    expect(first.status).toBe('candidate');

    // A candidate is listed under Watching, explained, and kept off the dashboard.
    await openTopics(page, 'Watching');
    const card = topicCard(page, title);
    await expect(card).toContainText('watching');
    await expect(card).toContainText('1 evidence item');
    await expect(card).toContainText('Watching for now');
    await expect(card.getByRole('button', { name: 'Confirm' })).toBeVisible();
    await page.getByLabel('Status').selectOption({ label: 'Active' });
    await expect(topicCard(page, title)).toHaveCount(0);
    await page.goto('/');
    await expect(topicFocus(page)).toBeVisible();
    await expect(focusEntry(page, title)).toHaveCount(0);

    // The same email again is not corroboration: still one distinct email, still watched.
    expect(await mention(username, stamp, title, 1)).toEqual({ id: first.id, status: 'candidate' });
    await openTopics(page, 'Watching');
    await expect(topicCard(page, title)).toContainText('1 evidence item');

    // A second, distinct email corroborates it: it becomes active everywhere.
    expect(await mention(username, stamp, title, 2)).toEqual({ id: first.id, status: 'active' });
    await openTopics(page, 'Watching');
    await expect(topicCard(page, title)).toHaveCount(0);
    await page.getByLabel('Status').selectOption({ label: 'Active' });
    await expect(topicCard(page, title)).toContainText('active');
    await expect(topicCard(page, title)).toContainText('2 evidence items');
    await expect(topicCard(page, title)).not.toContainText('Watching for now');

    await page.goto('/');
    const entry = focusEntry(page, title);
    await expect(entry).toContainText('active');
    await expect(entry).toContainText('2 evidence items');
    await expect(entry).toContainText('high');
  });

  test('confirming a watched topic promotes it without more mail', async ({ page }) => {
    const title = `Garden shed permit ${stamp}`;
    expect((await mention(username, stamp, title, 1)).status).toBe('candidate');

    await openTopics(page, 'Watching');
    await topicCard(page, title).getByRole('button', { name: 'Confirm' }).click();
    await expect(topicCard(page, title)).toHaveCount(0);
    await page.getByLabel('Status').selectOption({ label: 'Active' });
    await expect(topicCard(page, title)).toContainText('active');
    await expect(topicCard(page, title)).toContainText('1 evidence item');

    await page.goto('/');
    await expect(focusEntry(page, title)).toContainText('active');

    // A confirmed topic keeps its user-chosen status when more mail mentions it.
    await api(page, 'patch', `/api/topics/${(await mention(username, stamp, title, 2)).id}`, { status: 'resolved' });
    expect((await mention(username, stamp, title, 1)).status).toBe('resolved');
    await page.reload();
    await expect(topicFocus(page)).toBeVisible();
    await expect(focusEntry(page, title)).toHaveCount(0);
  });
});
