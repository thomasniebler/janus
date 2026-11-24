import type { Locator, Page } from '@playwright/test';
import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import { answerConfirm, api } from './helpers';

/*
 * Memory & Knowledge -> Agent Memory (JAN-224): search, create, edit, delete and compact
 * durable memory facts, and the active/archived filter. Every test runs as its own throwaway
 * user (JAN-336): compaction considers all of a user's active facts and the status card shows
 * the user's latest run, so facts or runs another test left behind must never be in play.
 */

interface Fact {
  id: number;
  summary: string;
  detail: string | null;
  source: string;
  status: string;
  compacted_into_id: number | null;
}

/**
 * Runs one compaction through the real `MemoryService._run_compaction`, with only the
 * utility model's answer scripted: it merges the given fact ids into one summary. The
 * e2e backend has no reachable model, so this is the only way to get a merge.
 */
const COMPACT_WITH_SCRIPTED_MODEL = `
from janus import llm_service
from janus.services.memory_service import MemoryService

source_ids, merged_summary = [int(value) for value in args[:-1]], args[-1]

class ScriptedUtilityModel:
    def suggest_memory_compactions(self, facts):
        return [{"source_ids": source_ids, "summary": merged_summary, "detail": "Merged by the e2e compaction script."}]

llm_service.build_llm_service = lambda *a, **k: ScriptedUtilityModel()
run = models.AgentMemoryCompactionRun(user_id=user.id)
db.add(run)
db.commit()
MemoryService._run_compaction(run.id)
`;

const listFacts = async (page: Page, query = ''): Promise<Fact[]> => {
  const params = new URLSearchParams({ include_archived: 'true', limit: '200' });
  if (query) params.set('query', query);
  const response = await page.request.get(`/api/memory/?${params}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
};

const createFact = async (page: Page, summary: string, detail?: string): Promise<Fact> =>
  (await api(page, 'post', '/api/memory/', { summary, detail: detail ?? null })).json();

/** The stored-facts card: everything below the search box. */
const storedFacts = (page: Page) =>
  page.locator('div.rounded-2xl').filter({ has: page.getByRole('button', { name: /^Delete memory fact/ }) });

const factCard = (page: Page, summary: string): Locator =>
  storedFacts(page).filter({ has: page.getByText(summary, { exact: true }) });

/** The Compaction status card (the heading sits in the card's title row). */
const compactionStatus = (page: Page) =>
  page.getByRole('heading', { name: 'Compaction status' }).locator('xpath=../..');

const search = (page: Page) => page.getByRole('textbox', { name: 'Search memory facts' });
const showArchived = (page: Page) => page.getByRole('checkbox', { name: 'Show archived' });

test.describe('Agent Memory (JAN-224)', () => {
  test.use({ asThrowawayUser: true });
  let stamp: string;

  test.beforeEach(async ({ page }) => {
    // One alphanumeric token, so it never splits into search words.
    stamp = `e2emem${Date.now()}`;
    await page.goto('/memory');
    await expect(page.getByRole('heading', { name: 'Agent Memory' })).toBeVisible();
  });

  test('creates a fact with detail, edits it, clears its detail, and deletes it', async ({ page }) => {
    const summary = `Prefers tea in the afternoon ${stamp}`;
    await page.getByLabel('Summary').fill(summary);
    await page.getByLabel('Detail').fill('Green tea, no sugar.');
    await page.getByRole('button', { name: 'Save fact' }).click();

    const card = factCard(page, summary);
    await expect(card).toBeVisible();
    await expect(card).toContainText('Green tea, no sugar.');
    await expect(card).toContainText('user');
    await expect(card).toContainText('active');
    // The form is cleared for the next fact.
    await expect(page.getByLabel('Summary').first()).toHaveValue('');

    // Edit: summary and detail are prefilled, and the change survives a reload.
    const edited = `Prefers coffee in the afternoon ${stamp}`;
    await page.getByRole('button', { name: `Edit memory fact "${summary}"` }).click();
    const editor = page.getByRole('form', { name: `Edit memory fact "${summary}"` });
    await expect(editor.getByLabel('Summary')).toHaveValue(summary);
    await expect(editor.getByLabel('Detail')).toHaveValue('Green tea, no sugar.');
    await editor.getByLabel('Summary').fill(edited);
    await editor.getByLabel('Detail').fill('Black, after lunch.');
    await editor.getByRole('button', { name: 'Save changes' }).click();
    await expect(editor).toHaveCount(0);
    await expect(factCard(page, edited)).toContainText('Black, after lunch.');
    await expect(page.getByText(summary, { exact: true })).toHaveCount(0);

    await page.reload();
    await expect(factCard(page, edited)).toContainText('Black, after lunch.');

    // A blank summary cannot be saved, Cancel throws the draft away.
    await page.getByRole('button', { name: `Edit memory fact "${edited}"` }).click();
    let form = page.getByRole('form', { name: `Edit memory fact "${edited}"` });
    await form.getByLabel('Summary').fill('   ');
    await expect(form.getByRole('button', { name: 'Save changes' })).toBeDisabled();
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(form).toHaveCount(0);
    await expect(factCard(page, edited)).toBeVisible();

    // Emptying the detail clears it.
    await page.getByRole('button', { name: `Edit memory fact "${edited}"` }).click();
    form = page.getByRole('form', { name: `Edit memory fact "${edited}"` });
    await form.getByLabel('Detail').fill('');
    await form.getByRole('button', { name: 'Save changes' }).click();
    await expect(form).toHaveCount(0);
    await expect(factCard(page, edited)).not.toContainText('Black, after lunch.');
    expect((await listFacts(page, edited)).find((fact) => fact.summary === edited)?.detail).toBeNull();

    // Delete asks first; Cancel keeps the fact.
    await page.getByRole('button', { name: `Delete memory fact "${edited}"` }).click();
    await answerConfirm(page, 'Delete memory fact?', 'Cancel');
    await expect(factCard(page, edited)).toBeVisible();
    await page.getByRole('button', { name: `Delete memory fact "${edited}"` }).click();
    await answerConfirm(page, 'Delete memory fact?', 'Delete');
    await expect(page.getByText(edited, { exact: true })).toHaveCount(0);
    await page.reload();
    await expect(page.getByText(edited, { exact: true })).toHaveCount(0);
  });

  test('editing a fact that was deleted elsewhere says so', async ({ page }) => {
    const summary = `Short-lived fact ${stamp}`;
    const fact = await createFact(page, summary);
    await page.reload();
    await page.getByRole('button', { name: `Edit memory fact "${summary}"` }).click();
    await api(page, 'delete', `/api/memory/${fact.id}`);

    const editor = page.getByRole('form', { name: `Edit memory fact "${summary}"` });
    await editor.getByLabel('Summary').fill(`Renamed ${stamp}`);
    await editor.getByRole('button', { name: 'Save changes' }).click();
    await expect(editor.getByRole('alert')).toHaveText('Memory fact not found');
    // The delete's `memory` change refetches the list without the fact; the editor and
    // the draft stay until the user cancels.
    await expect(page.getByText('No memory facts match the current filter.')).toHaveCount(0);
    await expect(editor).toBeVisible();
    await expect(editor.getByLabel('Summary')).toHaveValue(`Renamed ${stamp}`);
    await editor.getByRole('button', { name: 'Cancel' }).click();
    await expect(editor).toHaveCount(0);
    await expect(page.getByText(summary, { exact: true })).toHaveCount(0);
  });

  test('saving the same summary again refreshes the fact instead of duplicating it', async ({ page }) => {
    const summary = `Works from Lisbon in winter ${stamp}`;
    await createFact(page, summary, 'First note.');
    await page.reload();

    await page.getByLabel('Summary').fill(summary.toUpperCase());
    await page.getByLabel('Detail').fill('Second note.');
    await page.getByRole('button', { name: 'Save fact' }).click();

    await expect(factCard(page, summary)).toContainText('Second note.');
    await expect(storedFacts(page).filter({ hasText: new RegExp(stamp, 'i') })).toHaveCount(1);
  });

  // Regression: the duplicate check used the summary as a LIKE pattern, so `%` matched
  // any text and "50% spent" silently overwrote "50 percent spent".
  test('a summary with % or _ is its own fact, not a wildcard match of another', async ({ page }) => {
    const plain = `Budget is 50 percent spent ${stamp}`;
    const percent = `Budget is 50% spent ${stamp}`;
    const underscore = `Budget is 5_ percent spent ${stamp}`;
    await createFact(page, plain, 'The plain one.');
    await page.reload();

    for (const summary of [percent, underscore]) {
      await page.getByLabel('Summary').fill(summary);
      await page.getByRole('button', { name: 'Save fact' }).click();
      await expect(factCard(page, summary)).toBeVisible();
    }
    await expect(factCard(page, plain)).toContainText('The plain one.');
    const mine = (await listFacts(page)).filter((fact) => fact.summary.includes(stamp)).map((fact) => fact.summary);
    expect(mine.sort()).toEqual([plain, percent, underscore].sort());
  });

  test('search narrows the list and says when nothing matches', async ({ page }) => {
    const kayak = `Owns a red kayak ${stamp}`;
    const piano = `Plays the piano on Sundays ${stamp}`;
    await createFact(page, kayak);
    await createFact(page, piano, 'Mostly Chopin nocturnes.');
    await page.reload();

    // Search matches any of the words, so search for words only one of the facts has.
    await search(page).fill('kayak');
    await expect(factCard(page, kayak)).toBeVisible();
    await expect(page.getByText(piano, { exact: true })).toHaveCount(0);

    // The detail text is searched too.
    await search(page).fill('nocturnes');
    await expect(factCard(page, piano)).toBeVisible();
    await expect(page.getByText(kayak, { exact: true })).toHaveCount(0);

    await search(page).fill(`zzqx${stamp}nothing`);
    await expect(page.getByText('No memory facts match the current filter.')).toBeVisible();

    await search(page).fill('');
    await expect(factCard(page, kayak)).toBeVisible();
    await expect(factCard(page, piano)).toBeVisible();
  });

  test('compaction merges facts; the originals are archived and only shown with Show archived', async ({
    page,
    account,
  }) => {
    const first = await createFact(page, `Standup is at 9:30 ${stamp}`);
    const second = await createFact(page, `Daily standup happens at half past nine ${stamp}`);
    const merged = `Daily standup at 9:30 ${stamp}`;
    await runBackendPython(COMPACT_WITH_SCRIPTED_MODEL, [first.id, second.id, merged], { as: account.username });

    await page.reload();
    const status = compactionStatus(page);
    await expect(status).toContainText('Status: completed');
    await expect(status).toContainText('Compacted 2 facts into 1 merged memories.');
    await expect(status).toContainText('archived 2, created 1');

    // Active only: the merged fact is there, from compaction; the originals are not.
    await expect(showArchived(page)).not.toBeChecked();
    const mergedCard = factCard(page, merged);
    await expect(mergedCard).toContainText('compaction');
    await expect(mergedCard).toContainText('active');
    await expect(mergedCard).toContainText('Merged by the e2e compaction script.');
    await expect(page.getByText(first.summary, { exact: true })).toHaveCount(0);
    await expect(page.getByText(second.summary, { exact: true })).toHaveCount(0);

    // Show archived adds the originals, marked archived, after the active facts.
    await showArchived(page).check();
    for (const summary of [first.summary, second.summary]) {
      await expect(factCard(page, summary)).toContainText('archived');
    }
    await expect(factCard(page, merged)).toContainText('active');
    const mergedFact = (await listFacts(page)).find((fact) => fact.summary === merged);
    for (const original of await listFacts(page, stamp)) {
      if (original.id === first.id || original.id === second.id) {
        expect(original.status).toBe('archived');
        expect(original.compacted_into_id).toBe(mergedFact?.id);
      }
    }

    // Search respects the filter: an archived fact only matches with Show archived on.
    await search(page).fill('half past nine');
    await expect(factCard(page, second.summary)).toContainText('archived');
    await showArchived(page).uncheck();
    await expect(page.getByText(second.summary, { exact: true })).toHaveCount(0);
  });

  test('Compact Memory starts a run and shows how it ended', async ({ page }) => {
    await createFact(page, `Allergic to peanuts ${stamp}`);
    await createFact(page, `Has a peanut allergy ${stamp}`);
    await page.reload();
    // A fresh user has never compacted, so the status card cannot show an earlier run's end
    // as this one's (JAN-336: a run another test left behind did exactly that).
    await expect(compactionStatus(page)).toContainText('No compaction run yet.');

    const started = page.waitForResponse(
      (response) => response.url().endsWith('/api/memory/compact') && response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Compact Memory' }).click();
    const run = await (await started).json();
    expect(run.status).toBe('queued');

    // The e2e backend's utility model is unreachable, so the run ends without a merge;
    // either way it ends, the button comes back, and the facts are untouched. Wait for the
    // backend to finish this run, then check the card shows exactly how it ended.
    let latest: { id: number; status: string; error_message: string | null; result_summary: string | null } =
      run;
    await expect
      .poll(
        async () => {
          latest = await (await page.request.get('/api/memory/compact/latest')).json();
          return latest.status;
        },
        { timeout: 30_000 },
      )
      .toMatch(/^(completed|failed)$/);
    expect(latest.id).toBe(run.id);
    const status = compactionStatus(page);
    await expect(status).toContainText(`Status: ${latest.status}`);
    if (latest.status === 'failed') await expect(status).toContainText(`Error: ${latest.error_message}`);
    else await expect(status).toContainText(`Summary: ${latest.result_summary}`);
    await expect(page.getByRole('button', { name: 'Compact Memory' })).toBeEnabled();
    const mine = (await listFacts(page)).filter((fact) => fact.summary.includes(stamp));
    expect(mine.map((fact) => fact.status)).toEqual(['active', 'active']);
  });
});
