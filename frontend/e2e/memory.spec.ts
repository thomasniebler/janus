import { expect, test } from './fixtures';
import { answerConfirm, api, confirmDialog, resetUtilityModel, setCloudUtilityModel } from './helpers';

const EXTRACTION_WARNING = 'Send ingested text to a cloud model?';
const SYNTHESIS_WARNING = 'Send passages to a cloud model?';

test.describe('Memory & Knowledge page', () => {
  // Facts, topics, documents, knowledge and wiki are per user: as its own throwaway user each
  // test starts from empty lists (no entities, no wiki pages) whatever ran before (JAN-336).
  test.use({ asThrowawayUser: true });

  test('creates and deletes an agent memory fact', async ({ page }) => {
    const summary = `E2E memory fact ${Date.now()}`;

    await page.goto('/memory');
    await expect(page.getByRole('heading', { name: 'Agent Memory' })).toBeVisible();

    await page.getByLabel('Summary').fill(summary);
    await page.getByRole('button', { name: /save fact/i }).click();

    const factCard = page.getByText(summary, { exact: true });
    await expect(factCard).toBeVisible();

    await page.getByRole('button', { name: `Delete memory fact "${summary}"` }).click();
    await answerConfirm(page, 'Delete memory fact?', 'Delete');

    await expect(factCard).toHaveCount(0);
  });

  test('creates and deletes a manual topic', async ({ page }) => {
    const title = `E2E Topic ${Date.now()}`;

    await page.goto('/memory?tab=topics');
    await expect(page.getByRole('heading', { name: 'Topics', exact: true })).toBeVisible();

    await page.getByLabel('Manual topic title').fill(title);
    await page.getByLabel('Manual topic description').fill('Created by a Playwright e2e test.');
    await page.getByLabel('Manual topic marker keywords').fill('e2e-test-marker');
    await page.getByRole('button', { name: /save topic/i }).click();

    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    await page.getByRole('button', { name: `Delete topic "${title}"` }).click();
    await answerConfirm(page, 'Delete topic?', 'Delete');

    await expect(page.getByText(title)).toHaveCount(0);
  });

  test('uploads a document, shows it as ready, and deletes it', async ({ page }) => {
    const filename = `e2e-notes-${Date.now()}.md`;

    await page.goto('/memory?tab=documents');
    await expect(page.getByRole('heading', { name: 'Documents' })).toBeVisible();

    await page.getByLabel('Document file').setInputFiles({
      name: filename,
      mimeType: 'text/markdown',
      buffer: Buffer.from('# E2E notes\n\nUploaded by a Playwright test.'),
    });

    const sources = page.getByRole('list', { name: 'Knowledge sources' });
    const row = sources.getByRole('listitem').filter({ hasText: filename });
    await expect(row).toBeVisible();
    await expect(row.getByText('ready', { exact: true })).toBeVisible();
    await expect(row.getByText('1 chunk', { exact: true })).toBeVisible();

    await page.reload();
    await expect(page.getByRole('listitem').filter({ hasText: filename })).toBeVisible();

    await page.getByRole('button', { name: `Delete ${filename}` }).click();
    await answerConfirm(page, 'Delete source?', 'Delete');
    await expect(page.getByText(filename)).toHaveCount(0);
  });

  test('refuses an unsupported file with a visible reason', async ({ page }) => {
    await page.goto('/memory?tab=documents');
    // The picker filters by type, so bypass it the way a drag-and-drop would.
    await page.getByLabel('Document file').setInputFiles({
      name: 'picture.png',
      mimeType: 'image/png',
      buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });
    await expect(page.getByRole('alert')).toContainText('unsupported file type');
  });

  test('turns knowledge extraction on and off and saves a budget', async ({ page }) => {
    await page.goto('/memory?tab=knowledge');
    await expect(page.getByRole('heading', { name: 'Knowledge', exact: true })).toBeVisible();
    await expect(page.getByText(/No entities yet/)).toBeVisible();

    const budget = page.getByRole('spinbutton', { name: 'Monthly token budget' });
    await budget.fill('123000');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText(/of 123,000 tokens/)).toBeVisible();

    // The e2e utility model is local Ollama, so turning extraction on asks nothing.
    // The switch is controlled: it flips once the server has saved the change.
    await page.getByRole('checkbox').click();
    await expect(confirmDialog(page, EXTRACTION_WARNING)).toHaveCount(0);
    await expect(page.getByRole('checkbox')).toBeChecked();
    await expect(page.getByText('On', { exact: true })).toBeVisible();

    await page.reload();
    await expect(page.getByRole('checkbox')).toBeChecked();
    await expect(page.getByText(/of 123,000 tokens/)).toBeVisible();

    await page.getByRole('checkbox').click();
    await expect(page.getByRole('checkbox')).not.toBeChecked();
    await expect(page.getByText('Off', { exact: true })).toBeVisible();
  });

  test('finds an uploaded document through knowledge search', async ({ page }) => {
    const filename = `e2e-search-${Date.now()}.md`;
    await page.goto('/memory?tab=documents');
    await page.getByLabel('Document file').setInputFiles({
      name: filename,
      mimeType: 'text/markdown',
      buffer: Buffer.from('# Zanzibar offsite\n\nThe zanzibar offsite moves to November.'),
    });
    await expect(page.getByRole('listitem').filter({ hasText: filename }).getByText('ready', { exact: true })).toBeVisible();

    await page.goto('/memory?tab=knowledge');
    await page.getByLabel(/Search mail, chats/).fill('zanzibar');
    await page.getByRole('button', { name: 'Search' }).click();
    const results = page.getByRole('list', { name: 'Search results' });
    await expect(results.getByText(filename)).toBeVisible();
    await expect(results.getByText('matches your search')).toBeVisible();

    await page.getByLabel(/Search mail, chats/).fill('nothing-like-this-anywhere');
    await page.getByRole('button', { name: 'Search' }).click();
    await expect(page.getByText(/Nothing found for/)).toBeVisible();

    await page.goto('/memory?tab=documents');
    await page.getByRole('button', { name: `Delete ${filename}` }).click();
    await answerConfirm(page, 'Delete source?', 'Delete');
    await expect(page.getByText(filename)).toHaveCount(0);
  });

  test('turns wiki synthesis on and off and follows a topic to its wiki page', async ({ page }) => {
    await page.goto('/memory?tab=wiki');
    await expect(page.getByRole('heading', { name: 'Wiki', exact: true })).toBeVisible();
    await expect(page.getByText(/No pages yet/)).toBeVisible();

    const toggle = page.getByRole('checkbox', { name: 'Wiki synthesis' });
    await toggle.click();
    await expect(confirmDialog(page, SYNTHESIS_WARNING)).toHaveCount(0);
    await expect(toggle).toBeChecked();
    await page.reload();
    await expect(page.getByRole('checkbox', { name: 'Wiki synthesis' })).toBeChecked();
    await page.getByRole('checkbox', { name: 'Wiki synthesis' }).click();
    await expect(page.getByRole('checkbox', { name: 'Wiki synthesis' })).not.toBeChecked();

    const title = `E2E Wiki Topic ${Date.now()}`;
    await page.goto('/memory?tab=topics');
    await page.getByLabel('Manual topic title').fill(title);
    await page.getByLabel('Manual topic description').fill('Created by the wiki e2e test.');
    await page.getByLabel('Manual topic marker keywords').fill('e2e-wiki-marker');
    await page.getByRole('button', { name: /save topic/i }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    await page.locator('div.rounded-2xl').filter({ hasText: title }).getByRole('link', { name: 'Wiki page' }).first().click();
    await expect(page).toHaveURL(/tab=wiki&topic=\d+/);
    await expect(page.getByText(/This topic has no wiki page yet/)).toBeVisible();

    // Synthesis needs a model, so create the (empty) page through the API and edit it in the UI.
    const topicId = Number(new URL(page.url()).searchParams.get('topic'));
    await api(page, 'post', '/api/wiki/pages', { subject_type: 'topic', subject_id: topicId });
    await page.reload();
    const wikiPage = page.getByLabel('Wiki page');
    await expect(wikiPage.getByRole('heading', { name: title })).toBeVisible();
    await expect(wikiPage.getByText(/Not written yet/)).toBeVisible();

    for (const text of ['First version of my notes.', 'Second version of my notes.']) {
      await wikiPage.getByRole('button', { name: 'Edit' }).click();
      await wikiPage.getByLabel(/Page text/).fill(text);
      await wikiPage.getByRole('button', { name: 'Save' }).click();
      await expect(wikiPage.getByText(text)).toBeVisible();
    }
    await expect(wikiPage.getByText('Edited by you')).toBeVisible();

    await wikiPage.getByRole('button', { name: 'History' }).click();
    const revisions = wikiPage.getByRole('list', { name: 'Revisions' });
    await expect(revisions.getByRole('listitem')).toHaveCount(2);
    await revisions.getByRole('button', { name: 'Restore' }).click();
    await expect(wikiPage.getByText('First version of my notes.')).toBeVisible();
    await expect(revisions.getByRole('listitem')).toHaveCount(3);
    await page.reload();
    await expect(page.getByLabel('Wiki page').getByText('First version of my notes.')).toBeVisible();

    await page.goto('/memory?tab=topics');
    await page.getByRole('button', { name: `Delete topic "${title}"` }).click();
    await answerConfirm(page, 'Delete topic?', 'Delete');
    await expect(page.getByText(title)).toHaveCount(0);
  });

  test.describe('with a cloud utility model', () => {
    // The utility model is instance-wide, so the admin sets and resets it.
    test.beforeEach(async ({ adminRequest }) => {
      await setCloudUtilityModel(adminRequest);
    });

    test.afterEach(async ({ adminRequest }) => {
      await resetUtilityModel(adminRequest);
    });

    test('asks before knowledge extraction sends text to the cloud model', async ({ page }) => {
      await page.goto('/memory?tab=knowledge');
      await expect(page.getByText('openai/gpt-4o-mini is a cloud model', { exact: false })).toBeVisible();
      const extraction = page.getByRole('checkbox');
      await expect(extraction).not.toBeChecked();

      await extraction.click();
      const warning = confirmDialog(page, EXTRACTION_WARNING);
      await expect(warning).toBeVisible();
      await expect(warning).toContainText('openai/gpt-4o-mini');
      await answerConfirm(page, EXTRACTION_WARNING, 'Cancel');
      await expect(extraction).not.toBeChecked();
      await page.reload();
      await expect(page.getByRole('checkbox')).not.toBeChecked();

      await page.getByRole('checkbox').click();
      await answerConfirm(page, EXTRACTION_WARNING, 'Turn on');
      await expect(page.getByRole('checkbox')).toBeChecked();
      await page.reload();
      await expect(page.getByRole('checkbox')).toBeChecked();

      // Turning it off never asks.
      await page.getByRole('checkbox').click();
      await expect(confirmDialog(page, EXTRACTION_WARNING)).toHaveCount(0);
      await expect(page.getByRole('checkbox')).not.toBeChecked();
    });

    test('asks before wiki synthesis sends passages to the cloud model', async ({ page }) => {
      await page.goto('/memory?tab=wiki');
      const synthesis = page.getByRole('checkbox', { name: 'Wiki synthesis' });
      await expect(page.getByText('openai/gpt-4o-mini is a cloud model', { exact: false })).toBeVisible();
      await expect(synthesis).not.toBeChecked();

      await synthesis.click();
      const warning = confirmDialog(page, SYNTHESIS_WARNING);
      await expect(warning).toBeVisible();
      await expect(warning).toContainText('openai/gpt-4o-mini');
      await answerConfirm(page, SYNTHESIS_WARNING, 'Cancel');
      await expect(synthesis).not.toBeChecked();
      await page.reload();
      await expect(page.getByRole('checkbox', { name: 'Wiki synthesis' })).not.toBeChecked();

      await page.getByRole('checkbox', { name: 'Wiki synthesis' }).click();
      await answerConfirm(page, SYNTHESIS_WARNING, 'Turn on');
      await expect(page.getByRole('checkbox', { name: 'Wiki synthesis' })).toBeChecked();
      await page.reload();
      await expect(page.getByRole('checkbox', { name: 'Wiki synthesis' })).toBeChecked();

      await page.getByRole('checkbox', { name: 'Wiki synthesis' }).click();
      await expect(confirmDialog(page, SYNTHESIS_WARNING)).toHaveCount(0);
      await expect(page.getByRole('checkbox', { name: 'Wiki synthesis' })).not.toBeChecked();
    });
  });
});
