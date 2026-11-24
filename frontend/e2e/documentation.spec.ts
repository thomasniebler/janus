import { expect, test } from './fixtures';

test.describe('Documentation page', () => {
  test('renders the docs navigation and the default page', async ({ page }) => {
    await page.goto('/docs');

    await expect(page.getByRole('heading', { name: 'Documentation' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Architecture', exact: true })).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Getting Started with Janus', level: 1 })
    ).toBeVisible();
  });

  test('navigates to a different doc page via the sidebar', async ({ page }) => {
    await page.goto('/docs');

    await page.getByRole('link', { name: 'Architecture', exact: true }).click();

    await expect(page).toHaveURL(/\/docs\/architecture$/);
    await expect(page.getByRole('heading', { name: 'Architecture', level: 1 })).toBeVisible();
  });

  // JAN-225: the Markdown constructs the docs use render as structure, not as raw text.
  test('renders Markdown tables, code blocks, admonitions and tabs', async ({ page }) => {
    await page.goto('/docs/quickstart');
    const article = page.locator('article');

    await expect(article.getByRole('heading', { name: 'Quickstart', level: 1 })).toBeVisible();
    await expect(article.getByRole('heading', { name: 'Prerequisites', level: 2 })).toBeVisible();

    // GFM table
    const prerequisites = article.getByRole('table').first();
    await expect(prerequisites.getByRole('columnheader').first()).toBeVisible();
    await expect(prerequisites.getByRole('cell', { name: 'uv', exact: true })).toBeVisible();

    // Fenced code block
    await expect(article.locator('pre code').filter({ hasText: 'git clone https://github.com/thomasniebler/janus.git' })).toBeVisible();

    // `!!! note "Title"` admonition and `=== "Tab"` blocks are converted, not shown verbatim.
    await expect(article.locator('blockquote').filter({ hasText: 'NOTE: Single-instance runtime' })).toBeVisible();
    await expect(article.getByRole('heading', { name: 'Local desktop run', level: 3 })).toBeVisible();
    await expect(article.locator('pre code').filter({ hasText: 'uv run janus start' }).first()).toBeVisible();
    await expect(article).not.toContainText('!!! note');
    await expect(article).not.toContainText('=== "Local desktop run"');
  });

  test('opens the root TECH_SPEC.md from the in-app tech-spec index and follows its child links', async ({ page }) => {
    // `docs/docs/tech-spec/index.md` links the repository-root TECH_SPEC.md (`../../../TECH_SPEC.md`),
    // which lives outside `docs/docs` and is served through the `tech-spec` alias.
    await page.goto('/docs/tech-spec/index');
    const article = page.locator('article');
    await expect(article.getByRole('heading', { level: 1 })).toBeVisible();
    // Link names carry the prose style's backticks around inline code, so match by target.
    const rootSpecLink = article.locator('a[href="/docs/tech-spec"]').first();
    await expect(rootSpecLink).toContainText('TECH_SPEC.md');
    await rootSpecLink.click();

    await expect(page).toHaveURL(/\/docs\/tech-spec$/);
    await expect(article.getByRole('heading', { name: 'Janus Technical Specification', level: 1 })).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);

    // The spec map's links point at `docs/docs/tech-spec/*.md` and open in the viewer.
    const specMap = article.getByRole('table').first();
    const frontendSpec = specMap.locator('a[href="/docs/tech-spec/frontend"]');
    await expect(frontendSpec).toContainText('frontend.md');
    await frontendSpec.click();
    await expect(page).toHaveURL(/\/docs\/tech-spec\/frontend$/);
    await expect(article.getByRole('heading', { level: 1 })).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);

    // Back returns to the overview.
    await page.goBack();
    await expect(article.getByRole('heading', { name: 'Janus Technical Specification', level: 1 })).toBeVisible();
  });

  test('the Technical Spec overview entry in the sidebar opens TECH_SPEC.md', async ({ page }) => {
    await page.goto('/docs');
    // Two sections have an "Overview" entry; the Technical Spec one points at the alias route.
    const overview = page.locator('nav a[href="/docs/tech-spec"]');
    await expect(overview).toHaveText('Overview');
    await overview.click();
    await expect(page).toHaveURL(/\/docs\/tech-spec$/);
    await expect(page.getByRole('heading', { name: 'Janus Technical Specification', level: 1 })).toBeVisible();
  });

  test('a page that does not exist says so and links back to the start page', async ({ page }) => {
    const missing = page.waitForResponse((response) => response.url().endsWith('/api/docs/no-such-page.md'));
    await page.goto('/docs/no-such-page');
    expect((await missing).status()).toBe(404);

    const alert = page.getByRole('alert');
    await expect(alert).toContainText('Page not found');
    await expect(alert).toContainText('no-such-page');
    await alert.getByRole('link', { name: 'Go to the documentation start page' }).click();
    await expect(page).toHaveURL(/\/docs$/);
    await expect(page.getByRole('heading', { name: 'Getting Started with Janus', level: 1 })).toBeVisible();
  });

  test('the docs API is closed to a signed-out browser', async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
    try {
      for (const path of ['/api/docs/nav', '/api/docs/index.md', '/api/docs/tech-spec.md']) {
        const response = await context.request.get(path);
        expect(response.status(), path).toBe(401);
      }
      // The page itself sends a signed-out browser to the login screen.
      const page = await context.newPage();
      await page.goto('/docs');
      await expect(page).toHaveURL(/\/login/);
    } finally {
      await context.close();
    }
  });

  test('a path that escapes the docs directory is not served', async ({ page }) => {
    for (const path of ['/api/docs/..%2F..%2Fpyproject.toml', '/api/docs/..%2FREADME.md']) {
      const response = await page.request.get(path);
      expect(response.status(), path).toBe(404);
    }
  });
});
