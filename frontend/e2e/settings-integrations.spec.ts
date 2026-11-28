import { type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { FAKE_MCP_ACCESS_TOKEN, FAKE_MCP_CLIENT_ID, FakeIntegrations } from './fakeIntegrations';
import { api } from './helpers';

/**
 * Settings integrations (JAN-222): GitHub, Linear, web search, mail ingestion, and the
 * remote MCP registry with its OAuth PKCE flow, against the real backend. Secrets are
 * saved through the UI and must never come back in plaintext; the MCP server and its
 * OAuth provider are a loopback fake (`fakeIntegrations.ts`). The instance-wide cards
 * are read-only for a non-admin, and the backend refuses that user's writes.
 */
const succeeded = (page: Page, message: string) => page.getByRole('status').filter({ hasText: message });

/** A card on Connections -> Developer: the innermost block holding its heading and its own Save button. */
const integrationCard = (page: Page, title: string) =>
  page
    .locator('div')
    .filter({ has: page.getByRole('heading', { name: title, exact: true }) })
    .filter({ has: page.getByRole('button', { name: 'Save Settings' }) })
    .last();

test.describe('GitHub and Linear settings', () => {
  test.afterEach(async ({ page }) => {
    await api(page, 'patch', '/api/github/settings', { token: '', default_repo: '' });
    await api(page, 'patch', '/api/linear/settings', { api_key: '', default_team_id: '' });
  });

  test('saves the GitHub token write-only and clears it again', async ({ page }) => {
    const token = 'ghp_e2eSecretToken0123456789';
    await page.goto('/settings?tab=connections&sub=developer');
    const github = integrationCard(page, 'GitHub');
    await expect(page.getByText('No token has been stored yet.')).toBeVisible();
    await expect(github.getByRole('button', { name: 'Test Connection' })).toBeDisabled();

    await page.getByLabel('Personal Access Token').fill(token);
    await page.getByLabel('Default Repository').fill('octo-org/janus-e2e');
    await github.getByRole('button', { name: 'Save Settings' }).click();
    await expect(succeeded(page, 'GitHub settings saved.')).toBeVisible();
    await expect(page.getByText('Token is stored encrypted at rest.')).toBeVisible();

    const readBack = await page.request.get('/api/github/settings');
    expect(await readBack.text()).not.toContain(token);
    expect(await readBack.json()).toEqual({ token_configured: true, default_repo: 'octo-org/janus-e2e' });
    expect(await (await page.request.get('/api/planner/settings')).text()).not.toContain(token);

    await page.reload();
    await expect(page.getByLabel('Personal Access Token')).toHaveValue('');
    await expect(page.getByLabel('Personal Access Token')).toHaveAttribute('placeholder', /Token configured/);
    await expect(page.getByLabel('Default Repository')).toHaveValue('octo-org/janus-e2e');
    await expect(github.getByRole('button', { name: 'Test Connection' })).toBeEnabled();

    // Clearing used to send `null` ("unchanged"), so the token stayed while the card said saved.
    await page.getByRole('button', { name: 'Clear stored token' }).click();
    await page.getByLabel('Default Repository').fill('');
    await github.getByRole('button', { name: 'Save Settings' }).click();
    await expect(succeeded(page, 'GitHub settings saved.')).toBeVisible();
    await expect(page.getByText('No token has been stored yet.')).toBeVisible();
    expect(await (await page.request.get('/api/github/settings')).json()).toEqual({
      token_configured: false,
      default_repo: null,
    });
  });

  test('saves the Linear key write-only with a trimmed default team, then clears both', async ({ page }) => {
    const key = 'lin_api_e2eSecretKey0123456789';
    await page.goto('/settings?tab=connections&sub=developer');
    const linear = integrationCard(page, 'Linear');
    await expect(page.getByText('No API key has been stored yet.')).toBeVisible();

    await page.getByLabel('Personal API Key').fill(`  ${key}\n`);
    await page.getByLabel('Default Team (key or ID)').fill(' ENG ');
    await linear.getByRole('button', { name: 'Save Settings' }).click();
    await expect(succeeded(page, 'Linear settings saved.')).toBeVisible();
    await expect(page.getByText('API key is stored encrypted at rest.')).toBeVisible();

    const readBack = await page.request.get('/api/linear/settings');
    expect(await readBack.text()).not.toContain(key);
    expect(await readBack.json()).toEqual({ api_key_configured: true, default_team_id: 'ENG' });

    await page.reload();
    await expect(page.getByLabel('Personal API Key')).toHaveValue('');
    await expect(page.getByLabel('Personal API Key')).toHaveAttribute('placeholder', /API key configured/);
    await expect(page.getByLabel('Default Team (key or ID)')).toHaveValue('ENG');

    await page.getByRole('button', { name: 'Clear stored key' }).click();
    await page.getByLabel('Default Team (key or ID)').fill('');
    await linear.getByRole('button', { name: 'Save Settings' }).click();
    await expect(succeeded(page, 'Linear settings saved.')).toBeVisible();
    await expect(page.getByText('No API key has been stored yet.')).toBeVisible();
    expect(await (await page.request.get('/api/linear/settings')).json()).toEqual({
      api_key_configured: false,
      default_team_id: null,
    });
  });
});

test.describe('Web search settings', () => {
  test.afterEach(async ({ page }) => {
    await api(page, 'patch', '/api/web-search/settings', { provider: '', api_key: '', base_url: '' });
  });

  test('switches from SearXNG to Brave, keeps the key write-only, and turns it off', async ({ page }) => {
    const key = 'BSA-e2e-secret-key';
    await page.goto('/settings?tab=system&sub=capabilities');
    // The Web Search card: the innermost block holding its heading and its Save button
    // (the other cards on the tab name their Save buttons differently).
    const card = page
      .locator('div')
      .filter({ has: page.getByRole('heading', { name: 'Web Search' }) })
      .filter({ has: page.getByRole('button', { name: 'Save Settings' }) })
      .last();
    await expect(card.getByText('Not configured')).toBeVisible();

    await card.getByLabel('Provider', { exact: true }).selectOption('searxng');
    await card.getByLabel('SearXNG base URL').fill('not a url');
    await card.getByRole('button', { name: 'Save Settings' }).click();
    await expect(card.getByRole('alert')).toHaveText('Web search base URL must be an absolute http(s) URL.');

    await card.getByLabel('SearXNG base URL').fill('http://searxng.e2e.example:8888/');
    await card.getByRole('button', { name: 'Save Settings' }).click();
    await expect(succeeded(page, 'Web search settings saved.')).toBeVisible();
    await expect(card.getByText('Enabled', { exact: true })).toBeVisible();
    expect(await (await page.request.get('/api/web-search/settings')).json()).toMatchObject({
      provider: 'searxng',
      base_url: 'http://searxng.e2e.example:8888',
      configured: true,
    });

    await card.getByLabel('Provider', { exact: true }).selectOption('brave');
    await card.getByLabel('Brave Search API key').fill(key);
    await card.getByRole('button', { name: 'Save Settings' }).click();
    await expect(succeeded(page, 'Web search settings saved.')).toBeVisible();

    const readBack = await page.request.get('/api/web-search/settings');
    expect(await readBack.text()).not.toContain(key);
    expect(await readBack.json()).toMatchObject({ provider: 'brave', api_key_configured: true, configured: true });
    await page.reload();
    await expect(card.getByLabel('Provider', { exact: true })).toHaveValue('brave');
    await expect(card.getByLabel('Brave Search API key')).toHaveValue('');
    await expect(card.getByLabel('Brave Search API key')).toHaveAttribute('placeholder', /API key configured/);

    await card.getByLabel('Provider', { exact: true }).selectOption('');
    await card.getByRole('button', { name: 'Save Settings' }).click();
    await expect(card.getByText('Not configured')).toBeVisible();
  });

  test('offers the AI providers, shows the cloud hint, and keeps the model per provider', async ({ page }) => {
    await page.goto('/settings?tab=system&sub=capabilities');
    const card = page
      .locator('div')
      .filter({ has: page.getByRole('heading', { name: 'Web Search' }) })
      .filter({ has: page.getByRole('button', { name: 'Save Settings' }) })
      .last();
    const provider = card.getByLabel('Provider', { exact: true });

    await expect(provider.locator('option')).toHaveText([
      'Disabled',
      'SearXNG (self-hosted, stays local)',
      'Brave Search API (third party)',
      'OpenAI',
      'Google Gemini',
      'Anthropic',
      'OpenRouter',
    ]);
    await provider.selectOption('anthropic');
    await expect(card.getByLabel('Search model')).toHaveAttribute('placeholder', /claude/);
    await expect(card.getByTestId('web-search-cloud-hint')).toContainText('sent to Anthropic');
    // Native providers reuse the chat key: no separate key field.
    await expect(card.getByLabel(/api key/i)).toHaveCount(0);

    await card.getByLabel('Search model').fill('claude-e2e-model');
    await card.getByRole('button', { name: 'Save Settings' }).click();
    await expect(succeeded(page, 'Web search settings saved.')).toBeVisible();
    expect(await (await page.request.get('/api/web-search/settings')).json()).toMatchObject({
      provider: 'anthropic',
      model: 'claude-e2e-model',
    });
    await page.reload();
    await expect(card.getByLabel('Search model')).toHaveValue('claude-e2e-model');
  });
});

test.describe('Mail ingestion settings', () => {
  let initialLimit: number;

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    initialLimit = (await (await page.request.get('/api/emails/settings')).json()).initial_sync_email_limit;
  });

  test.afterEach(async ({ page }) => {
    await api(page, 'patch', '/api/emails/settings', { initial_sync_email_limit: initialLimit });
  });

  test('saves a new initial sync limit, refuses an invalid one, and keeps it across a reload', async ({ page }) => {
    await page.goto('/settings?tab=connections&sub=mail');
    const limit = page.getByLabel('Initial Sync Email Limit');
    const save = page.getByRole('button', { name: 'Save Mail Ingestion Settings' });
    await expect(limit).toHaveValue(String(initialLimit));

    await limit.fill('0');
    await expect(page.getByText('Enter a whole number greater than 0.')).toBeVisible();
    await expect(save).toBeDisabled();

    await limit.fill('37');
    await save.click();
    await expect(succeeded(page, 'Mail ingestion settings saved.')).toBeVisible();

    await page.reload();
    await expect(page.getByLabel('Initial Sync Email Limit')).toHaveValue('37');
    // The same instance-wide value the IMAP sync reads, also visible in the system settings.
    expect((await (await page.request.get('/api/planner/settings')).json()).initial_sync_email_limit).toBe(37);
  });
});

test.describe.serial('Remote MCP servers', () => {
  const fake = new FakeIntegrations();
  const providerName = 'E2E MCP provider';

  test.beforeAll(async () => {
    await fake.start();
  });

  test.afterAll(async ({ browser }) => {
    await fake.stop();
    const page = await browser.newPage();
    await page.goto('/');
    const servers: { id: number; provider_name: string }[] = await (await page.request.get('/api/tools/servers')).json();
    for (const server of servers.filter((row) => row.provider_name.startsWith('E2E'))) {
      await api(page, 'delete', `/api/tools/servers/${server.id}`);
    }
    await page.close();
  });

  const serverRow = (page: Page, name: string) =>
    page.locator('div.rounded-xl', { has: page.getByRole('button', { name: new RegExp(`^${name}`) }) }).last();

  const openForm = async (page: Page) => {
    await page.goto('/settings?tab=tools');
    await page.getByRole('button', { name: 'Add MCP server' }).click();
  };

  test('refuses an endpoint on a private network', async ({ page }) => {
    await openForm(page);
    await page.getByLabel('Provider name').fill('E2E private endpoint');
    await page.getByLabel('HTTP MCP endpoint').fill('http://10.0.0.1/mcp');
    await page.getByRole('button', { name: 'Save server' }).click();

    await expect(page.getByText('The MCP endpoint resolves to a blocked network address.')).toBeVisible();
    await expect(page.getByRole('button', { name: /^E2E private endpoint/ })).toHaveCount(0);
  });

  test('registers a server, authorizes it through OAuth PKCE, and discovers its tools', async ({ page }) => {
    await openForm(page);
    await page.getByLabel('Provider name').fill(providerName);
    await page.getByLabel('HTTP MCP endpoint').fill(`${fake.url}/mcp`);
    await page.getByLabel('OAuth authorization endpoint (optional)').fill(`${fake.url}/oauth/authorize`);
    await page.getByLabel('OAuth token endpoint (optional)').fill(`${fake.url}/oauth/token`);
    await page.getByLabel('OAuth client ID (optional)').fill(FAKE_MCP_CLIENT_ID);
    await page.getByLabel('OAuth scopes (optional)').fill('tools.read');
    await page.getByRole('button', { name: 'Save server' }).click();

    const row = serverRow(page, providerName);
    await expect(row.getByText('Not discovered')).toBeVisible();
    await expect(row.getByText(`${fake.url}/mcp`)).toBeVisible();

    // Without a token the server answers 401, so discovery asks for authorization.
    await row.getByRole('button', { name: 'Discover', exact: true }).click();
    await expect(row.getByText('Authorization required')).toBeVisible();
    await expect(row.getByText('Remote MCP server requires authorization.')).toBeVisible();

    // Authorize: the browser goes to the provider, which redirects back to the backend's
    // callback; the backend redeems the code with its PKCE verifier and lands on Settings.
    await row.getByRole('button', { name: 'Authorize', exact: true }).click();
    await expect(page.getByText('MCP server authorized successfully.')).toBeVisible();
    await expect(page).toHaveURL(/\/settings\?tab=tools$/);
    expect(fake.tokenExchanges.at(-1)).toEqual({
      pkceVerified: true,
      redirectUri: `${new URL(page.url()).origin}/api/tools/oauth/callback`,
    });
    // The browser only ever carried the S256 challenge; the verifier went from the backend to the token endpoint.
    const { query } = fake.requestsTo('/oauth/authorize').at(-1)!;
    expect(query.get('code_challenge_method')).toBe('S256');
    expect(query.get('scope')).toBe('tools.read');
    expect(query.has('code_verifier')).toBe(false);

    await serverRow(page, providerName).getByRole('button', { name: 'Discover', exact: true }).click();
    const ready = serverRow(page, providerName);
    await expect(ready.getByText('Healthy')).toBeVisible();
    expect(fake.requestsTo('/mcp').at(-1)!.headers.authorization).toBe(`Bearer ${FAKE_MCP_ACCESS_TOKEN}`);

    await ready.getByRole('button', { name: new RegExp(`^${providerName}`) }).click();
    await expect(ready.getByText('e2e_lookup', { exact: true })).toBeVisible();
    await expect(ready.getByText('e2e_status', { exact: true })).toBeVisible();
    // Chat offers them; only the record lookup is marked read-only, so the others ask first (JAN-343).
    await expect(ready.getByText('Asks before each call in chat')).toHaveCount(3);
    await expect(ready.getByText('Read-only: runs without asking in chat')).toHaveCount(1);

    // The registry never returns the OAuth tokens.
    const registry = await (await page.request.get('/api/tools/registry')).text();
    expect(registry).not.toContain(FAKE_MCP_ACCESS_TOKEN);
    expect(registry).not.toContain('e2e-mcp-refresh-token');
  });

  test('uses a static bearer token, reports a rejected one, and never returns it (JAN-342)', async ({ page }) => {
    const name = 'E2E MCP bearer';
    await openForm(page);
    await page.getByLabel('Provider name').fill(name);
    await page.getByLabel('HTTP MCP endpoint').fill(`${fake.url}/mcp`);
    await page.getByLabel('Bearer token (optional)').fill('e2e-wrong-token');
    await page.getByRole('button', { name: 'Save server' }).click();

    const row = serverRow(page, name);
    await expect(row.getByTestId(/^mcp-credential-/)).toHaveText('Bearer token');
    await row.getByRole('button', { name: 'Discover', exact: true }).click();
    await expect(row.getByText('Token rejected')).toBeVisible();
    await expect(row.getByText('Remote MCP server rejected the bearer token. Save a new token for this server.')).toBeVisible();
    await expect(row.getByRole('button', { name: 'Authorize', exact: true })).toHaveCount(0);

    await row.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    await row.getByLabel('Replace bearer token').fill(FAKE_MCP_ACCESS_TOKEN);
    await row.getByRole('button', { name: 'Save token' }).click();
    await expect(row.getByText('Not discovered')).toBeVisible();
    await row.getByRole('button', { name: 'Discover', exact: true }).click();
    await expect(row.getByText('Healthy')).toBeVisible();
    expect(fake.requestsTo('/mcp').at(-1)!.headers.authorization).toBe(`Bearer ${FAKE_MCP_ACCESS_TOKEN}`);

    const registry = await (await page.request.get('/api/tools/registry')).text();
    expect(registry).not.toContain('e2e-wrong-token');
    expect(registry).not.toContain(FAKE_MCP_ACCESS_TOKEN);

    await row.getByRole('button', { name: 'Remove token' }).click();
    await expect(row.getByTestId(/^mcp-credential-/)).toHaveText('No credential');
  });

  test('a denied authorization says so and stores no token', async ({ page }) => {
    const name = 'E2E MCP denied';
    await page.goto('/');
    await api(page, 'post', '/api/tools/servers', {
      provider_name: name,
      endpoint: `${fake.url}/mcp`,
      transport: 'streamable_http',
      oauth_authorization_endpoint: `${fake.url}/oauth/authorize`,
      oauth_token_endpoint: `${fake.url}/oauth/token`,
      oauth_client_id: FAKE_MCP_CLIENT_ID,
    });
    await page.goto('/settings?tab=tools');
    const row = serverRow(page, name);
    await row.getByRole('button', { name: 'Discover', exact: true }).click();
    await expect(row.getByText('Authorization required')).toBeVisible();

    const exchanges = fake.tokenExchanges.length;
    fake.denyNextAuthorization = true;
    await row.getByRole('button', { name: 'Authorize', exact: true }).click();
    await expect(page.getByText('MCP authorization could not be completed. Please try again.')).toBeVisible();
    expect(fake.tokenExchanges.length).toBe(exchanges);

    await serverRow(page, name).getByRole('button', { name: 'Discover', exact: true }).click();
    await expect(serverRow(page, name).getByText('Authorization required')).toBeVisible();
  });
});

test.describe('Settings as a non-admin', () => {
  test.use({ asThrowawayUser: true });

  test('sees the instance-wide integrations read-only and cannot change them', async ({ page: member }) => {

    await member.goto('/settings?tab=system');
    await expect(member.getByText('Only an administrator can test or change the AI backend.')).toBeVisible();
    await expect(member.getByRole('button', { name: 'Test Selected Backend' })).toHaveCount(0);
    await expect(member.getByRole('button', { name: 'Save Backend Settings' })).toHaveCount(0);

    for (const [field, message] of [
      ['Personal Access Token', 'Only an administrator can change GitHub settings.'],
      ['Personal API Key', 'Only an administrator can change Linear settings.'],
    ] as const) {
      await member.goto('/settings?tab=connections&sub=developer');
      await expect(member.getByText(message, { exact: false })).toBeVisible();
      await expect(member.getByLabel(field)).toBeDisabled();
      await expect(member.getByRole('button', { name: 'Save Settings' })).toHaveCount(0);
    }

    await member.goto('/settings?tab=system&sub=capabilities');
    await expect(member.getByText('Only an administrator can change web search settings.')).toBeVisible();
    await expect(member.getByLabel('Provider', { exact: true })).toBeDisabled();

    await member.goto('/settings?tab=connections&sub=mail');
    await expect(member.getByText('Only an administrator can change mail ingestion settings.')).toBeVisible();
    await expect(member.getByLabel('Initial Sync Email Limit')).toBeDisabled();
    await expect(member.getByRole('button', { name: 'Save Mail Ingestion Settings' })).toHaveCount(0);

    // The backend refuses the member's writes too, not only the UI.
    const { csrf_token: csrfToken } = await (await member.request.get('/api/auth/csrf')).json();
    const headers = { 'X-CSRF-Token': csrfToken };
    for (const [url, data] of [
      ['/api/github/settings', { token: 'ghp_member' }],
      ['/api/linear/settings', { api_key: 'lin_api_member' }],
      ['/api/web-search/settings', { provider: 'searxng', base_url: 'http://member.example' }],
      ['/api/emails/settings', { initial_sync_email_limit: 1 }],
      ['/api/planner/settings', { llm_provider: 'openai' }],
    ] as const) {
      expect((await member.request.patch(url, { data, headers })).status(), url).toBe(403);
    }
    expect((await member.request.post('/api/planner/test-llm', { data: {}, headers })).status()).toBe(403);
  });
});
