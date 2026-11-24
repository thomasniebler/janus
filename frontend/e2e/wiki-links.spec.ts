import { type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { api } from './helpers';

// JAN-331: Janus writes Obsidian-style `[[Page Title]]` cross-references in wiki pages and chat replies.
// They render as links to /memory?tab=wiki&wiki=<title>, which opens the page with that title. Pages are
// created through the API (synthesis needs a model); the chat reply is replayed over page.routeWebSocket
// because the e2e account has no LLM provider.

const PLACEHOLDER = 'Ask a question...';

/** Creates a manual topic with a wiki page and returns the page's title. */
const createWikiPage = async (page: Page, title: string, body?: string) => {
  const topic = await (
    await api(page, 'post', '/api/topics/', {
      title,
      summary: 'Created by the wiki-links e2e test.',
      marker_keywords: ['e2e-wiki-links'],
    })
  ).json();
  const wikiPage = await (await api(page, 'post', '/api/wiki/pages', { subject_type: 'topic', subject_id: topic.id })).json();
  if (body) await api(page, 'put', `/api/wiki/pages/${wikiPage.id}`, { body });
  return wikiPage.title as string;
};

test.describe('Wiki links', () => {
  // Topics and wiki pages (which cannot be deleted) stay with a throwaway user (JAN-336).
  test.use({ asThrowawayUser: true });

  test('a [[link]] in a wiki page opens the page it names', async ({ page }) => {
    const stamp = Date.now();
    const target = await createWikiPage(page, `E2E Link Target ${stamp}`, 'The target page body.');
    const source = await createWikiPage(
      page,
      `E2E Link Source ${stamp}`,
      `This page points at [[${target}|the target page]] and at [[E2E Missing ${stamp}]].`,
    );

    await page.goto(`/memory?tab=wiki&wiki=${encodeURIComponent(source)}`);
    const wikiPage = page.getByLabel('Wiki page');
    await expect(wikiPage.getByRole('heading', { name: source })).toBeVisible();

    await wikiPage.getByRole('link', { name: 'the target page' }).click();
    await expect(page).toHaveURL(new RegExp(`tab=wiki&wiki=${encodeURIComponent(target)}$`));
    await expect(page.getByLabel('Wiki page').getByRole('heading', { name: target })).toBeVisible();
    await expect(page.getByLabel('Wiki page').getByText('The target page body.')).toBeVisible();

    // Back to the source, then a link to a page that does not exist yet.
    await page.goBack();
    await expect(page.getByLabel('Wiki page').getByRole('heading', { name: source })).toBeVisible();
    await page.getByLabel('Wiki page').getByRole('link', { name: `E2E Missing ${stamp}` }).click();
    await expect(page.getByText(`No wiki page called “E2E Missing ${stamp}” yet.`)).toBeVisible();
    await expect(page.getByLabel('Search pages')).toHaveValue(`E2E Missing ${stamp}`);
  });

  test('a [[link]] in a chat reply opens the wiki page', async ({ page }) => {
    const target = await createWikiPage(page, `E2E Chat Link ${Date.now()}`, 'Opened from chat.');
    await page.routeWebSocket(/\/ws\/chat/, (ws) => {
      ws.onMessage((raw) => {
        const text = typeof raw === 'string' ? raw : raw.toString();
        if (!text.includes('wiki')) return;
        ws.send('__CONVERSATION_ID__:433310');
        ws.send('__START__');
        ws.send(`It is all on the [[${target}]] page.`);
        ws.send('__END__');
      });
    });

    await page.goto('/');
    const sidebar = page.getByRole('complementary', { name: 'Chat with Janus' });
    const input = sidebar.getByPlaceholder(PLACEHOLDER);
    await expect(input).toBeVisible();
    await input.fill('Where is that in the wiki?');
    await input.press('Enter');

    const link = sidebar.getByRole('link', { name: target });
    await expect(link).toHaveAttribute('href', `/memory?tab=wiki&wiki=${encodeURIComponent(target)}`);
    await link.click();
    await expect(page).toHaveURL(/\/memory\?tab=wiki&wiki=/);
    await expect(page.getByLabel('Wiki page').getByRole('heading', { name: target })).toBeVisible();
    await expect(page.getByLabel('Wiki page').getByText('Opened from chat.')).toBeVisible();
    // The in-app navigation kept the chat (no full page reload).
    await expect(sidebar.getByText('Where is that in the wiki?')).toBeVisible();
  });
});
