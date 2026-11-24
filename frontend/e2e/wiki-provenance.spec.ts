import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { api, signInAsNewUser } from './helpers';

// JAN-334: the wiki's Sources tab (provenance), drill-down from a [n] marker to the raw passage or memory
// fact, and the Graph view of topics and entities. Topics, pages, and facts are created through the real API.
// Citations only exist after wiki synthesis (which needs a model the e2e account does not have), so the
// drill-down test adds citations to the real page and provenance responses with page.route.

/** Creates an active manual topic with a wiki page; returns both ids and the title. */
const createTopicPage = async (page: Page, title: string) => {
  const topic = await (
    await api(page, 'post', '/api/topics/', {
      title,
      summary: 'Created by the knowledge provenance e2e test.',
      marker_keywords: ['e2e-provenance'],
    })
  ).json();
  const wikiPage = await (
    await api(page, 'post', '/api/wiki/pages', { subject_type: 'topic', subject_id: topic.id })
  ).json();
  return { topicId: topic.id as number, pageId: wikiPage.id as number, title: wikiPage.title as string };
};

// The Sources tab refetches provenance when it remounts, so a routed request can still be in flight when a
// test ends; drop the routes first so such a handler does not fail the test on a disposed response.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' });
});

// Every test runs as its own throwaway user (JAN-336): wiki pages cannot be deleted, so pages
// and topics created here never reach the shared e2e admin's wiki or graph.
test.use({ asThrowawayUser: true });

test.describe('Knowledge provenance', () => {
  test('the Sources tab lists the memory facts about a page’s topic', async ({ page }) => {
    const { title } = await createTopicPage(page, `E2E Provenance ${Date.now()}`);
    await api(page, 'post', '/api/memory/', { summary: `${title} is owned by the platform team` });

    await page.goto(`/memory?tab=wiki&wiki=${encodeURIComponent(title)}`);
    const wikiPage = page.getByLabel('Wiki page');
    await expect(wikiPage.getByRole('heading', { name: title })).toBeVisible();
    await wikiPage.getByRole('tab', { name: 'Sources' }).click();
    await expect(wikiPage.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
    await expect(wikiPage.getByText('This page cites nothing yet.')).toBeVisible();
    const facts = wikiPage.getByRole('list', { name: 'Linked facts' });
    await expect(facts).toContainText(`${title} is owned by the platform team`);
    await expect(facts).toContainText('not cited yet');
  });

  test('a [n] marker drills down to the raw passage and memory fact behind it', async ({ page }) => {
    const { pageId, title } = await createTopicPage(page, `E2E Drill Down ${Date.now()}`);
    await api(page, 'put', `/api/wiki/pages/${pageId}`, { body: 'The renewal is due on 1 November [1]. The budget is 40k [2].' });

    await page.route(`**/api/wiki/pages/${pageId}`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.citations = [
        { n: 1, chunk_id: 900, source_id: 900, source_type: 'email', title: 'Renewal mail', date: '2026-09-20T08:00:00' },
        { n: 2, fact_id: 901, source_type: 'memory_fact', title: 'Budget is capped at 40k', date: '2026-09-18T10:00:00' },
      ];
      await route.fulfill({ response, json: body });
    });
    await page.route(`**/api/wiki/pages/${pageId}/provenance`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.citations = [
        {
          n: 1, kind: 'passage', available: true, title: 'Renewal mail', date: '2026-09-20T08:00:00', source_type: 'email',
          chunk_id: 900, fact_id: null, source: { id: 900, type: 'email', title: 'Renewal mail' },
          excerpt: 'Hi, Acme asks us to sign the renewal by 1 November.', fact: null, entity_ids: [],
        },
        {
          n: 2, kind: 'fact', available: true, title: 'Budget is capped at 40k', date: '2026-09-18T10:00:00',
          source_type: 'memory_fact', chunk_id: null, fact_id: 901, source: null, excerpt: null, entity_ids: [],
          fact: { id: 901, summary: 'Budget is capped at 40k', detail: 'Agreed with finance.', status: 'active', citations: [2] },
        },
      ];
      await route.fulfill({ response, json: body });
    });

    await page.goto(`/memory?tab=wiki&wiki=${encodeURIComponent(title)}`);
    const wikiPage = page.getByLabel('Wiki page');
    await expect(wikiPage.getByRole('heading', { name: title })).toBeVisible();
    await wikiPage.getByRole('link', { name: '[1]' }).first().click();
    await expect(wikiPage.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
    await expect(wikiPage.getByLabel('Passage 1')).toContainText('sign the renewal by 1 November');

    await wikiPage.getByRole('tab', { name: 'Page' }).click();
    await wikiPage.getByRole('link', { name: '[2]' }).first().click();
    await expect(wikiPage.getByRole('list', { name: 'Citations' })).toContainText('Agreed with finance.');
  });

  test('the Graph view shows topics and opens a topic’s wiki page', async ({ page }) => {
    const { title } = await createTopicPage(page, `E2E Graph ${Date.now()}`);
    await page.goto('/memory?tab=wiki');
    await page.getByRole('button', { name: 'Graph', exact: true }).click();
    const graph = page.getByRole('group', { name: 'Knowledge graph' });
    await expect(graph).toBeVisible();

    await graph.getByRole('button', { name: `Topic: ${title}` }).click();
    await expect(page.getByLabel('Selected node')).toContainText(title);
    await page.getByRole('button', { name: 'Open wiki page' }).click();
    await expect(page.getByRole('list', { name: 'Wiki pages' })).toBeVisible();
    await expect(page.getByLabel('Wiki page').getByRole('heading', { name: title })).toBeVisible();

    await page.getByRole('button', { name: 'Graph', exact: true }).click();
    await page.getByRole('button', { name: 'Show table' }).click();
    await expect(page.getByRole('table', { name: 'Knowledge graph connections' })).toBeVisible();
  });
});

/** Failed queries retry three times with backoff (TanStack Query's default) before the error shows. */
const QUERY_RETRY_TIMEOUT = 20_000;

/** The page's own response with these citations, so `[n]` markers in its text become drill-down links. */
const withCitations = async (page: Page, pageId: number, citations: object[]) =>
  page.route(`**/api/wiki/pages/${pageId}`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), citations } });
  });

/** The provenance response with these resolved citations and entities (the rest is the real answer). */
const withProvenance = async (page: Page, pageId: number, overrides: object) =>
  page.route(`**/api/wiki/pages/${pageId}/provenance`, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), ...overrides } });
  });

test.describe('Knowledge provenance: edge cases', () => {
  test('drill-down focuses only the followed marker, and a deleted source is marked gone', async ({ page }) => {
    const { pageId, title } = await createTopicPage(page, `E2E Gone ${Date.now()}`);
    await api(page, 'put', `/api/wiki/pages/${pageId}`, {
      body: 'The old contract ended [1]. Acme signed the renewal [2]. Unknown marker [7].',
    });
    await withCitations(page, pageId, [
      { n: 1, chunk_id: 910, source_id: 910, source_type: 'email', title: 'Old contract mail', date: '2026-01-02T08:00:00' },
      { n: 2, chunk_id: 911, source_id: 911, source_type: 'upload', title: 'Renewal.pdf', date: '2026-09-20T08:00:00' },
    ]);
    await withProvenance(page, pageId, {
      citations: [
        {
          n: 1, kind: 'passage', available: false, title: 'Old contract mail', date: '2026-01-02T08:00:00',
          source_type: 'email', chunk_id: 910, fact_id: null, source: null, excerpt: null, fact: null, entity_ids: [],
        },
        {
          n: 2, kind: 'passage', available: true, title: 'Renewal.pdf', date: '2026-09-20T08:00:00', source_type: 'upload',
          chunk_id: 911, fact_id: null, source: { id: 911, type: 'upload', title: 'Renewal.pdf' },
          excerpt: 'Acme signed the renewal for two more years.', fact: null, entity_ids: [9101],
        },
      ],
      entities: [{ id: 9101, name: 'Acme Corp', type: 'organization', citations: [2], is_subject: false }],
    });

    await page.goto(`/memory?tab=wiki&wiki=${encodeURIComponent(title)}`);
    const wikiPage = page.getByLabel('Wiki page');
    await expect(wikiPage.getByRole('heading', { name: title })).toBeVisible();
    // A marker without a citation stays plain text.
    await expect(wikiPage.getByRole('link', { name: '[7]' })).toHaveCount(0);
    await expect(wikiPage.getByText(/Unknown marker \[7\]/)).toBeVisible();

    await wikiPage.getByRole('link', { name: '[1]' }).first().click();
    const citations = wikiPage.getByRole('list', { name: 'Citations' });
    const gone = citations.locator('#wiki-provenance-1');
    await expect(gone).toHaveAttribute('aria-current', 'true');
    await expect(gone).toContainText('gone');
    await expect(gone).toContainText('This passage no longer exists');
    const second = citations.locator('#wiki-provenance-2');
    await expect(second).not.toHaveAttribute('aria-current', 'true');
    await expect(second.getByRole('button')).toHaveAttribute('aria-expanded', 'false');
    await expect(wikiPage.getByLabel('Passage 2')).toHaveCount(0);

    await second.getByRole('button').click();
    await expect(wikiPage.getByLabel('Passage 2')).toContainText('two more years');
    await expect(second).toContainText('Mentions: Acme Corp');
    const entities = wikiPage.getByRole('list', { name: 'Linked entities' });
    await expect(entities).toContainText('Acme Corp');
    await expect(entities).toContainText('[2]');

    // Opening the Sources tab directly highlights nothing.
    await wikiPage.getByRole('tab', { name: 'Page' }).click();
    await wikiPage.getByRole('tab', { name: 'Sources' }).click();
    await expect(citations.locator('[aria-current="true"]')).toHaveCount(0);
  });

  test('the Sources tab shows when provenance cannot be loaded', async ({ page }) => {
    const { pageId, title } = await createTopicPage(page, `E2E Provenance Error ${Date.now()}`);
    await page.route(`**/api/wiki/pages/${pageId}/provenance`, (route) =>
      route.fulfill({ status: 500, json: { detail: 'boom' } }),
    );
    await page.goto(`/memory?tab=wiki&wiki=${encodeURIComponent(title)}`);
    const wikiPage = page.getByLabel('Wiki page');
    await wikiPage.getByRole('tab', { name: 'Sources' }).click();
    await expect(wikiPage.getByRole('alert')).toHaveText('Could not load the sources of this page.', {
      timeout: QUERY_RETRY_TIMEOUT,
    });
  });

  test('graph nodes select from the keyboard, and a failed load is reported', async ({ page }) => {
    const { title } = await createTopicPage(page, `E2E Graph Keys ${Date.now()}`);
    await page.goto('/memory?tab=wiki');
    await page.getByRole('button', { name: 'Graph', exact: true }).click();
    const node = page.getByRole('group', { name: 'Knowledge graph' }).getByRole('button', { name: `Topic: ${title}` });
    await node.focus();
    await page.keyboard.press('Enter');
    await expect(node).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByLabel('Selected node')).toContainText(title);
    await page.keyboard.press(' ');
    await expect(node).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByText('Select a topic or entity to see its links.')).toBeVisible();

    await page.route('**/api/knowledge/graph**', (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }));
    await page.reload();
    await page.getByRole('button', { name: 'Graph', exact: true }).click();
    await expect(page.getByRole('alert')).toHaveText('Could not load the knowledge graph.', {
      timeout: QUERY_RETRY_TIMEOUT,
    });
  });

  test('another user sees an empty graph and never this user’s pages, facts, or provenance', async ({ browser, page, adminRequest }) => {
    const { pageId, title } = await createTopicPage(page, `E2E Private ${Date.now()}`);
    await api(page, 'post', '/api/memory/', { summary: `${title} private fact` });
    const { context, page: other } = await signInAsNewUser(browser, adminRequest, 'e2e-prov');
    try {
      expect((await other.request.get(`/api/wiki/pages/${pageId}/provenance`)).status()).toBe(404);
      expect(JSON.stringify(await (await other.request.get('/api/knowledge/graph')).json())).not.toContain(title);

      await other.goto(`/memory?tab=wiki&wiki=${encodeURIComponent(title)}`);
      await expect(other.getByText(`No wiki page called “${title}” yet.`)).toBeVisible();
      await expect(other.getByLabel('Wiki page')).toHaveCount(0);

      await other.getByRole('button', { name: 'Graph', exact: true }).click();
      await expect(other.getByText(/Nothing to show yet/)).toBeVisible();
      await expect(other.getByRole('group', { name: 'Knowledge graph' })).toHaveCount(0);
      await expect(other.getByText(title, { exact: true })).toHaveCount(0);
      await expect(other.getByText(`${title} private fact`)).toHaveCount(0);
    } finally {
      await context.close();
    }
  });
});
