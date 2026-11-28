import type { Page } from '@playwright/test';
import { UI_VERSION_HTML_CLASSES, UI_VERSION_STORAGE_KEY, type UIVersion } from '../src/contexts/uiVersion';
import { runBackendPython } from './backendSeed';
import { expect, test } from './fixtures';
import { answerConfirm, api, confirmDialog, deferred } from './helpers';
import { isBriefingRequest, LLMStub, type StubRequest } from './llm-stub';

test.describe('Settings page', () => {
  // Semantic search is instance-wide: put it back to off even when a test stops half-way,
  // so no later spec tries to reach an embedding provider.
  test.afterEach(async ({ adminRequest }) => {
    await api(adminRequest, 'patch', '/api/planner/settings', {
      embedding_provider: null,
      embedding_model: null,
      embedding_min_similarity: 0.5,
    });
  });

  test('defaults to the AI & Models tab and groups the pages under headings', async ({ page }) => {
    await page.goto('/settings');

    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'AI & Models' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tab', { name: 'Models', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page).toHaveURL(/tab=system&sub=models/);
    const nav = page.getByRole('tablist', { name: 'Settings sections' });
    for (const group of ['Setup', 'Assistant', 'Automation', 'Account', 'Monitoring']) {
      await expect(nav.getByText(group, { exact: true })).toBeVisible();
    }
  });

  test('the Connections tab splits mail, calendars, messaging and developer accounts into sub-tabs', async ({ page }) => {
    await page.goto('/settings');
    await page.getByRole('tab', { name: 'Connections', exact: true }).click();

    await expect(page).toHaveURL(/tab=connections&sub=mail/);
    await expect(page.getByRole('heading', { name: 'Connected Accounts' })).toBeVisible();
    await expect(page.getByText('No email accounts connected.')).toBeVisible();

    await page.getByRole('tab', { name: 'Messaging' }).click();
    await expect(page).toHaveURL(/sub=messaging/);
    await expect(page.getByRole('heading', { name: 'WhatsApp Setup' })).toBeVisible();
    await expect(page.getByTestId('telegram-config')).toBeVisible();

    await page.getByRole('tab', { name: 'Developer' }).click();
    await expect(page.getByRole('heading', { name: 'GitHub', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Linear', exact: true })).toBeVisible();
  });

  for (const { legacy, expected } of [
    { legacy: 'accounts', expected: /tab=connections&sub=mail/ },
    { legacy: 'mail-ingestion', expected: /tab=connections&sub=mail/ },
    { legacy: 'whatsapp', expected: /tab=connections&sub=messaging/ },
    { legacy: 'github', expected: /tab=connections&sub=developer/ },
    { legacy: 'linear', expected: /tab=connections&sub=developer/ },
  ]) {
    test(`an old ?tab=${legacy} link lands on its new place`, async ({ page }) => {
      await page.goto(`/settings?tab=${legacy}`);

      await expect(page.getByRole('tab', { name: 'Connections', exact: true })).toHaveAttribute('aria-selected', 'true');
      await expect(page).toHaveURL(expected);
    });
  }

  test('switches to the Connections tab and offers Google and shows Microsoft as not set up', async ({ page }) => {
    await page.goto('/settings');

    await page.getByRole('tab', { name: 'Connections', exact: true }).click();
    await page.getByRole('tab', { name: 'Calendar & tasks' }).click();

    await expect(page).toHaveURL(/tab=connections/);
    await expect(page.getByRole('tab', { name: 'Connections', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('heading', { name: 'Google', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Microsoft', exact: true })).toBeVisible();
    // The e2e backend is configured with a Google client (env.ts) but no Microsoft client.
    await expect(page.getByRole('button', { name: 'Connect Google' })).toBeEnabled();
    await expect(page.getByText('Microsoft is not set up on this server')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Connect Microsoft' })).toBeDisabled();
  });

  // The OAuth callbacks redirect to /settings?error=<code> (JAN-318): each code opens the
  // Connections tab, shows its own message, and is removed from the URL.
  for (const { code, message } of [
    {
      code: 'google_invalid_client',
      message: 'Google rejected the Client ID or Client Secret. Check google_client_id and google_client_secret in settings.toml.',
    },
    {
      code: 'google_redirect_uri_mismatch',
      message:
        'The redirect URI is not registered for this Google OAuth client. Add the redirect URIs shown on the Connections tab in Google Cloud Console.',
    },
  ]) {
    test(`shows the Google callback error ${code} on the Connections tab and clears it from the URL`, async ({ page }) => {
      await page.goto(`/settings?error=${code}`);

      await expect(page.getByRole('tab', { name: 'Connections', exact: true })).toHaveAttribute('aria-selected', 'true');
      await expect(page.getByRole('heading', { name: 'Google', exact: true })).toBeVisible();
      await expect(page.getByText(message)).toBeVisible();
      await expect.poll(() => new URL(page.url()).searchParams.has('error')).toBe(false);
      // The cleaned-up URL still reflects the active tab.
      await expect(page).toHaveURL(/tab=connections/);
    });
  }

  test('switches to the System Status and System Events tabs', async ({ page }) => {
    await page.goto('/settings');

    await page.getByRole('tab', { name: 'System Status' }).click();
    await expect(page).toHaveURL(/tab=status/);
    await expect(page.getByRole('heading', { name: 'System Status' })).toBeVisible();
    await expect(page.getByText('Overall status')).toBeVisible();

    await page.getByRole('tab', { name: 'System Events' }).click();
    await expect(page).toHaveURL(/tab=events/);
    await expect(page.getByRole('heading', { name: 'System Events' })).toBeVisible();

    // The events table's <th> cells don't get an ARIA columnheader role in this
    // layout, so assert on the header text directly instead of by role.
    const table = page.locator('table');
    await expect(table.getByText('Time', { exact: true })).toBeVisible();
    await expect(table.getByText('Source', { exact: true })).toBeVisible();
    await expect(table.getByText('Message', { exact: true })).toBeVisible();

    // Background workers (planner sync, LLM init, etc.) log startup events, so the
    // table has at least one real row beyond the header on a freshly started backend.
    await expect(table.locator('tbody tr').first()).toBeVisible();
  });

  test('the Stats tab shows per-day charts, approval rates, and switches range (JAN-258, JAN-371)', async ({ page }) => {
    await page.goto('/settings');

    await page.getByRole('tab', { name: 'Stats' }).click();
    await expect(page).toHaveURL(/tab=stats/);
    await expect(page.getByRole('heading', { name: 'Stats' })).toBeVisible();
    await expect(page.getByTestId('stat-New mail')).toBeVisible();
    await expect(page.getByText('New mail per day')).toBeVisible();
    await expect(page.getByText('Trigger runs per day')).toBeVisible();
    // JAN-371: approval rates for each kind of Janus proposal.
    await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible();
    await expect(page.getByTestId('stat-Suggested automations')).toContainText(/\d+ approved · \d+ rejected · \d+ open/);
    await expect(page.getByTestId('stat-Tool approvals in chat')).toContainText(/\d+ allowed · \d+ denied · \d+ timed out/);
    await expect(page.getByTestId('stat-Character changes')).toContainText(/\d+ approved/);
    // JAN-408: LLM token usage by surface and model.
    await expect(page.getByRole('heading', { name: 'LLM usage' })).toBeVisible();
    await expect(page.getByTestId('stat-Model calls')).toBeVisible();

    const statsRequest = page.waitForRequest((request) => request.url().includes('/api/stats') && request.url().includes('days=7'));
    await page.getByRole('button', { name: '7 days' }).click();
    await statsRequest;
    await expect(page.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true');

    const mailChart = page.getByTestId('daily-chart-New mail per day');
    await mailChart.getByRole('button', { name: 'Show table' }).click();
    await expect(mailChart.locator('tbody tr')).toHaveCount(7);
  });

  test('Recent runs on the Triggers tab is filterable and paginated (JAN-256)', async ({ page }) => {
    await page.goto('/settings?tab=runtime');

    await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();
    const filtered = page.waitForRequest(
      (request) => request.url().includes('/api/runtime/triggers/') && request.url().includes('status=failed')
    );
    await page.getByRole('combobox', { name: 'Filter runs by status' }).selectOption('failed');
    await filtered;
    // Other specs leave real runs behind (the briefing spec's run fails against
    // the unreachable model), so a range long before any run is the empty case.
    const ranged = page.waitForRequest(
      (request) => request.url().includes('/api/runtime/triggers/') && request.url().includes('end=2001-')
    );
    await page.getByLabel('From').fill('2001-01-01T00:00');
    await page.getByLabel('Until').fill('2001-01-02T00:00');
    await ranged;
    await expect(page.getByText('No runs match these filters.')).toBeVisible();
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await expect(page.getByRole('combobox', { name: 'Filter runs by status' })).toHaveValue('');
  });

  test('the Security tab blocks new passwords over the 72-byte bcrypt limit', async ({ page }) => {
    // Never submitted: a real password change would revoke the shared e2e session.
    let changePasswordCalls = 0;
    await page.route('**/api/auth/change-password', (route) => {
      changePasswordCalls += 1;
      return route.abort();
    });
    await page.goto('/settings');
    await page.getByRole('tab', { name: 'Security' }).click();

    const submit = page.getByRole('button', { name: 'Update Password' });
    await page.getByLabel('Current Password').fill('whatever');
    await page.getByLabel('New Password', { exact: true }).fill('new-secret');
    await page.getByLabel('Repeat New Password').fill('new-secret');
    await expect(submit).toBeEnabled();

    // 37 two-byte characters = 74 UTF-8 bytes, even though it is only 37 characters.
    const tooLong = 'é'.repeat(37);
    await page.getByLabel('New Password', { exact: true }).fill(tooLong);
    await page.getByLabel('Repeat New Password').fill(tooLong);

    await expect(page.getByText('New password must be at most 72 bytes long.').first()).toBeVisible();
    await expect(submit).toBeDisabled();
    expect(changePasswordCalls).toBe(0);
  });

  test('configures semantic search, keeps it across a reload, and turns it off again', async ({ page }) => {
    await page.goto('/settings?tab=system&sub=capabilities');
    await expect(page.getByRole('heading', { name: 'Semantic search' })).toBeVisible();
    // Every control below is unique on the Search & images tab.
    const card = page;
    const provider = card.getByLabel('Embedding provider');
    const save = card.getByRole('button', { name: 'Save semantic search' });

    await expect(provider).toHaveValue('');
    await provider.selectOption('ollama');
    await expect(card.getByLabel('Embedding model')).toHaveAttribute('placeholder', 'nomic-embed-text');
    await expect(card.getByTestId('embedding-cloud-hint')).toHaveCount(0);

    // The similarity cutoff is an expert setting behind Advanced.
    const advanced = card.getByRole('button', { name: 'Advanced' });
    await expect(advanced).toHaveAttribute('aria-expanded', 'false');
    await advanced.click();
    await card.getByLabel('Minimum similarity (0–1)').fill('0.65');

    await save.click();
    await expect(card.getByText('Semantic search settings saved.')).toBeVisible();

    await page.reload();
    await expect(provider).toHaveValue('ollama');
    await expect(card.getByLabel('Embedding model')).toHaveValue('nomic-embed-text');
    // A stored non-default cutoff opens Advanced on its own.
    await expect(card.getByLabel('Minimum similarity (0–1)')).toHaveValue('0.65');

    // Restore the defaults so later tests never try to reach an embedding provider.
    // (Off hides the provider fields, Advanced included, so reset the cutoff first.)
    await card.getByLabel('Minimum similarity (0–1)').fill('0.5');
    await provider.selectOption('');
    await save.click();
    await expect(card.getByText('Semantic search settings saved.')).toBeVisible();
    await page.reload();
    await expect(provider).toHaveValue('');
  });

  test('deletes a skill only after the in-app confirmation (JAN-292)', async ({ page }) => {
    const name = `e2e-skill-${Date.now()}`;
    await api(page, 'post', '/api/skills', {
      name,
      description: 'Created by a Playwright e2e test.',
      content: '# E2E skill\n\nNothing to see here.',
    });

    await page.goto('/settings?tab=skills');
    await expect(page.getByRole('tab', { name: 'Skills' })).toHaveAttribute('aria-selected', 'true');
    const row = page.getByRole('row').filter({ hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: `Delete ${name}`, exact: true }).click();
    await expect(confirmDialog(page, 'Delete skill?')).toContainText(`delete the skill "${name}"?`);
    await answerConfirm(page, 'Delete skill?', 'Cancel');
    await expect(row).toBeVisible();
    await page.reload();
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: `Delete ${name}`, exact: true }).click();
    await answerConfirm(page, 'Delete skill?', 'Delete');
    await expect(row).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('tab', { name: 'Skills' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('row').filter({ hasText: name })).toHaveCount(0);
  });

  test('tests the selected AI backend and lists its models (JAN-142)', async ({ page }) => {
    // No model runtime is reachable from the e2e backend, so the lookup usually comes back
    // with an empty list and the connection test with status "error"; both answers are
    // validated by the API client, and a schema failure fails this test (see fixtures.ts).
    const modelList = page.waitForResponse(
      (response) => response.url().endsWith('/api/planner/llm-models') && response.request().method() === 'POST'
    );
    await page.goto('/settings?tab=system');
    const lookup = await (await modelList).json();
    expect(Array.isArray(lookup.models)).toBeTruthy();
    if (lookup.models.length === 0) {
      await expect(page.getByText('Could not load model list — enter a model name manually.').first()).toBeVisible();
    }

    const tested = page.waitForResponse(
      (response) => response.url().endsWith('/api/planner/test-llm') && response.request().method() === 'POST'
    );
    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    const result = await (await tested).json();
    expect(typeof result.message).toBe('string');
    await expect(page.getByText(result.message, { exact: true })).toBeVisible();
  });
});

// Per-user settings (JAN-336): trigger definitions and the per-tool switches belong to the user
// who sets them, so these tests run as a throwaway user and never leave a switched-off tool or
// a trigger on the shared e2e admin when they fail half-way.
test.describe('Settings page: per-user settings', () => {
  test.use({ asThrowawayUser: true });

  test('creates an event trigger from the backend event catalog and keeps it across a reload (JAN-210)', async ({ page }) => {
    const name = `E2E mail triage ${Date.now()}`;
    const catalogLoaded = page.waitForResponse(
      (response) => response.url().includes('/api/runtime/definitions/event-catalog') && response.ok()
    );
    await page.goto('/settings?tab=runtime');
    await catalogLoaded;

    await page.getByRole('button', { name: 'New trigger' }).click();
    const editor = page.locator('form').filter({ has: page.getByRole('button', { name: 'Save trigger' }) });
    await editor.getByLabel('Name', { exact: true }).fill(name);
    await editor.getByLabel(/objective/i).fill('Summarize each new email in one line.');
    await editor.getByLabel('Activation').selectOption('event');

    // Only catalogued values are offered: the subsystem first, then its events.
    const subsystem = editor.getByLabel('Subsystem');
    const event = editor.getByLabel('Event', { exact: true });
    await expect(subsystem.locator('option:not([disabled])')).toHaveText(['Mail', 'WhatsApp', 'Planner', 'Webhooks', 'Telegram']);
    await subsystem.selectOption('whatsapp');
    await expect(event.locator('option:not([disabled])')).toHaveText([
      'New direct message',
      'New group message',
      'Mentioned in a group',
    ]);
    await subsystem.selectOption('mail');
    await expect(event.locator('option:not([disabled])')).toHaveText(['New email received']);
    await event.selectOption('mail.received');
    await expect(editor.getByTestId('trigger-event-details')).toContainText('Each run receives: email_id');

    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/runtime/definitions/') && response.request().method() === 'POST'
    );
    await editor.getByRole('button', { name: 'Save trigger' }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    expect((await response.json()).event_type).toBe('mail.received');

    await page.reload();
    const card = page.locator('[data-testid^="trigger-definition-"]').filter({ hasText: name });
    await expect(card).toHaveCount(1);
    await expect(card).toContainText('On event: Mail · New email received');

    // Clean up so the shared e2e database stays as other specs expect it.
    await card.getByRole('button', { name: `Delete ${name}` }).click();
    await answerConfirm(page, 'Delete trigger?', 'Delete');
    await expect(card).toHaveCount(0);
  });

  test('filters an event trigger by payload values and limits its runs per hour, then clears the filter (JAN-363)', async ({
    page,
  }) => {
    const name = `E2E family group ${Date.now()}`;
    const catalogLoaded = page.waitForResponse(
      (response) => response.url().includes('/api/runtime/definitions/event-catalog') && response.ok()
    );
    await page.goto('/settings?tab=runtime');
    await catalogLoaded;

    await page.getByRole('button', { name: 'New trigger' }).click();
    const editor = page.locator('form').filter({ has: page.getByRole('button', { name: 'Save trigger' }) });
    await editor.getByLabel('Name', { exact: true }).fill(name);
    await editor.getByLabel(/objective/i).fill('Tell me what the family group decided.');
    await editor.getByLabel('Activation').selectOption('event');
    await editor.getByLabel('Subsystem').selectOption('whatsapp');
    await editor.getByLabel('Event', { exact: true }).selectOption('whatsapp.group_message_received');
    // A new trigger starts with the event's default limit.
    const limit = editor.getByLabel('Max runs per hour');
    await expect(limit).toHaveValue('10');

    // Each value is added on its own, so a chat title with a comma stays one value.
    await editor.getByRole('button', { name: 'Add condition' }).click();
    await editor.getByLabel('Field of condition 1').selectOption('chat');
    const values = editor.getByLabel('Values of condition 1');
    await values.fill('Family, friends');
    await values.press('Enter');
    await values.fill('Climbing club');
    await editor.getByRole('button', { name: 'Add value to condition 1' }).click();
    await expect(editor.getByRole('button', { name: 'Remove Family, friends' })).toBeVisible();
    await expect(editor.getByRole('button', { name: 'Remove Climbing club' })).toBeVisible();

    const created = page.waitForResponse(
      (response) => response.url().endsWith('/api/runtime/definitions/') && response.request().method() === 'POST'
    );
    await editor.getByRole('button', { name: 'Save trigger' }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    const body = await response.json();
    expect(body.event_filter).toEqual([{ field: 'chat', op: 'in', values: ['Family, friends', 'Climbing club'] }]);
    expect(body.max_runs_per_hour).toBe(10);

    await page.reload();
    const card = page.locator('[data-testid^="trigger-definition-"]').filter({ hasText: name });
    await expect(card).toHaveCount(1);
    await expect(card.getByTestId('trigger-event-options-summary')).toHaveText(
      'Only when chat is one of Family, friends, Climbing club · max 10 runs/hour'
    );

    // Editing shows the stored values; lower the limit and clear the filter.
    await card.getByRole('button', { name: 'Edit' }).click();
    const editForm = page.locator('form').filter({ has: page.getByRole('button', { name: 'Save trigger' }) });
    await expect(editForm.getByRole('button', { name: 'Remove Family, friends' })).toBeVisible();
    await expect(editForm.getByLabel('Max runs per hour')).toHaveValue('10');
    await editForm.getByLabel('Max runs per hour').fill('2');
    await editForm.getByRole('button', { name: 'Remove condition 1' }).click();
    await expect(editForm.getByLabel('Field of condition 1')).toHaveCount(0);
    const updated = page.waitForResponse(
      (response) => /\/api\/runtime\/definitions\/\d+$/.test(response.url()) && response.request().method() === 'PATCH'
    );
    await editForm.getByRole('button', { name: 'Save trigger' }).click();
    const patched = await updated;
    expect(patched.status()).toBe(200);
    expect((await patched.json()).event_filter).toBeNull();

    await page.reload();
    await expect(card).toHaveCount(1);
    await expect(card.getByTestId('trigger-event-options-summary')).toHaveText('max 2 runs/hour');

    // Clean up so the shared e2e database stays as other specs expect it.
    await card.getByRole('button', { name: `Delete ${name}` }).click();
    await answerConfirm(page, 'Delete trigger?', 'Delete');
    await expect(card).toHaveCount(0);
  });

  test('raises one run limit and switches the other off, then resets both to the default', async ({ page }) => {
    const name = `E2E deep dive ${Date.now()}`;
    await page.goto('/settings?tab=runtime');
    await page.getByRole('button', { name: 'New trigger' }).click();
    const editor = page.locator('form').filter({ has: page.getByRole('button', { name: 'Save trigger' }) });
    await editor.getByLabel('Name', { exact: true }).fill(name);
    await editor.getByLabel(/objective/i).fill('Look into one open question in depth.');

    const rounds = editor.getByLabel('Tool-calling rounds limit');
    const runTime = editor.getByLabel('Run time limit');
    await expect(rounds).toHaveValue('default');
    await expect(runTime).toHaveValue('default');
    await rounds.selectOption('custom');
    await editor.getByLabel('Tool-calling rounds', { exact: true }).fill('25');
    await runTime.selectOption('off');
    await expect(editor.getByText(/Without a limit a run continues until the model finishes/)).toBeVisible();

    const created = page.waitForResponse(
      (response) => response.url().endsWith('/api/runtime/definitions/') && response.request().method() === 'POST'
    );
    await editor.getByRole('button', { name: 'Save trigger' }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    const body = await response.json();
    expect([body.max_tool_calls, body.max_run_seconds]).toEqual([25, 0]);

    await page.reload();
    const card = page.locator('[data-testid^="trigger-definition-"]').filter({ hasText: name });
    await expect(card).toContainText('Limits: 25 rounds, no time limit');

    // Back to the defaults: both columns are cleared, not set to a number.
    await card.getByRole('button', { name: 'Edit' }).click();
    const editForm = page.locator('form').filter({ has: page.getByRole('button', { name: 'Save trigger' }) });
    await expect(editForm.getByLabel('Tool-calling rounds', { exact: true })).toHaveValue('25');
    await editForm.getByLabel('Tool-calling rounds limit').selectOption('default');
    await editForm.getByLabel('Run time limit').selectOption('default');
    const updated = page.waitForResponse(
      (response) => /\/api\/runtime\/definitions\/\d+$/.test(response.url()) && response.request().method() === 'PATCH'
    );
    await editForm.getByRole('button', { name: 'Save trigger' }).click();
    const patched = await (await updated).json();
    expect([patched.max_tool_calls, patched.max_run_seconds]).toEqual([null, null]);
    await expect(card).toContainText('Limits: 8 rounds, 180 s');

    await card.getByRole('button', { name: `Delete ${name}` }).click();
    await answerConfirm(page, 'Delete trigger?', 'Delete');
    await expect(card).toHaveCount(0);
  });

  test('switches a tool for unattended runs, warns for write tools, and resets', async ({ page }) => {
    await page.goto('/settings?tab=tools');
    const tools = page.getByRole('list', { name: 'Built-in tool permissions' });
    await expect(tools).toBeVisible();

    // Trigger and event-log tools are no longer locked (JAN-272, JAN-274): they get a normal switch, on by default.
    await expect(tools.getByRole('combobox', { name: 'Unattended: Trigger tools' })).toHaveValue('allowed');
    await expect(tools.getByRole('combobox', { name: 'Unattended: System introspection tools' })).toHaveValue('allowed');

    const memory = tools.getByRole('combobox', { name: 'Unattended: Memory tools' });
    await expect(memory).toHaveValue('allowed');
    await memory.selectOption('off');
    await expect(tools.getByRole('button', { name: 'Reset Memory tools' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'Unattended: Memory tools' })).toHaveValue('off');
    await page.getByRole('button', { name: 'Reset Memory tools' }).click();
    await expect(page.getByRole('combobox', { name: 'Unattended: Memory tools' })).toHaveValue('allowed');

    // Allowing a write tool asks first in an in-app dialog; declining leaves it off.
    const send = page.getByRole('combobox', { name: 'Unattended: Send mail' });
    await expect(send).toHaveValue('off');
    await send.selectOption('allowed');
    const confirm = page.getByRole('dialog', { name: 'Allow unattended use?' });
    await expect(confirm).toContainText('with nobody watching');
    await confirm.getByRole('button', { name: 'Cancel' }).click();
    await expect(confirm).toHaveCount(0);
    await expect(send).toHaveValue('off');
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'Unattended: Send mail' })).toHaveValue('off');

    // Confirming saves it; reset restores the default.
    await page.getByRole('combobox', { name: 'Unattended: Send mail' }).selectOption('allowed');
    await confirm.getByRole('button', { name: 'Allow' }).click();
    await expect(confirm).toHaveCount(0);
    // Wait until the save has landed and the list refetched before reloading.
    await expect(page.getByRole('button', { name: 'Reset Send mail' })).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Unattended: Send mail' })).toHaveValue('allowed');
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'Unattended: Send mail' })).toHaveValue('allowed');
    await page.getByRole('button', { name: 'Reset Send mail' }).click();
    await expect(page.getByRole('combobox', { name: 'Unattended: Send mail' })).toHaveValue('off');
  });

  test('switches a confirmation-required tool off in chat, keeps it across a reload, and resets (JAN-306)', async ({
    page,
  }) => {
    await page.goto('/settings?tab=tools');
    const tools = page.getByRole('list', { name: 'Built-in tool permissions' });
    await expect(tools).toBeVisible();

    // Only tools that ask before every call have the switch; the rest simply run in chat.
    await expect(tools.getByRole('combobox', { name: 'In chat: Memory tools' })).toHaveCount(0);
    await expect(tools.getByLabel('In chat: Memory tools')).toHaveText('In chat: Allowed');

    const chat = tools.getByRole('combobox', { name: 'In chat: Send mail' });
    await expect(chat).toHaveValue('ask');
    await expect(chat.locator('option')).toHaveText(['Ask every time', 'Always allow', 'Off in chat']);
    await chat.selectOption('off');
    // Restrictive change: no warning dialog.
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(tools.getByRole('button', { name: 'Reset Send mail' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'In chat: Send mail' })).toHaveValue('off');
    // The unattended switch is independent and keeps its default.
    await expect(page.getByRole('combobox', { name: 'Unattended: Send mail' })).toHaveValue('off');

    await page.getByRole('button', { name: 'Reset Send mail' }).click();
    await expect(page.getByRole('combobox', { name: 'In chat: Send mail' })).toHaveValue('ask');
    await expect(page.getByRole('button', { name: 'Reset Send mail' })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'In chat: Send mail' })).toHaveValue('ask');
  });
});

// JAN-225: Settings → Appearance switches the app shell. `public/ui-shell-bootstrap.js`,
// loaded blocking from `index.html`'s <head>, applies the cached shell's `<html>` class
// before the React bundle runs, so a reload never paints the wrong shell first. Each test
// runs as its own throwaway user, so a parallel worker's admin never renders another shell.
test.describe('Appearance: app shell switching', () => {
  const SHELL_NAMES: Record<UIVersion, RegExp> = {
    v1: /Classic \(UIv1\)/,
    v2: /Terminal \(UIv2\)/,
    v3: /Rusty Console \(UIv3\)/,
  };

  /** Checks `<html>` carries this shell's identity class and no other shell's. */
  const expectShellClass = async (page: Page, shell: UIVersion) => {
    const html = page.locator('html');
    await expect(html).toHaveClass(/\bdark\b/);
    for (const [version, className] of Object.entries(UI_VERSION_HTML_CLASSES)) {
      if (!className) continue;
      if (version === shell) await expect(html).toHaveClass(new RegExp(`\\b${className}\\b`));
      else await expect(html).not.toHaveClass(new RegExp(`\\b${className}\\b`));
    }
  };

  const shellOfClass = (className: string): UIVersion => {
    const classes = className.split(/\s+/);
    const match = Object.entries(UI_VERSION_HTML_CLASSES).find(([, name]) => name && classes.includes(name));
    return (match?.[0] as UIVersion | undefined) ?? 'v1';
  };

  /**
   * Loads the page with the app bundle held back, checks `<html>` already carries the
   * shell's class while only the bootstrap script has run, then lets the app load and
   * checks the class never named another shell at any point of the load.
   */
  const loadWithoutFlash = async (page: Page, shell: UIVersion, url?: string) => {
    const bundle = deferred();
    const appBundle = /\/assets\/index-[^/]+\.js$/;
    await page.route(appBundle, async (route) => {
      await bundle.promise;
      await route.continue();
    });
    if (url) await page.goto(url, { waitUntil: 'commit' });
    else await page.reload({ waitUntil: 'commit' });

    await expectShellClass(page, shell);
    await expect(page.locator('#root')).toBeEmpty();

    bundle.release();
    await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();
    await page.unroute(appBundle);
    await expectShellClass(page, shell);
    const seen = await page.evaluate(() => (window as unknown as { __e2eHtmlClasses: string[] }).__e2eHtmlClasses);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.map(shellOfClass)).toEqual(seen.map(() => shell));
  };

  const cachedShell = (page: Page) =>
    page.evaluate((key) => window.localStorage.getItem(key), UI_VERSION_STORAGE_KEY);

  test.beforeEach(async ({ page }) => {
    // Every class `<html>` takes from the first script on (init scripts run before index.html's own).
    await page.addInitScript(() => {
      const seen: string[] = [];
      (window as unknown as { __e2eHtmlClasses: string[] }).__e2eHtmlClasses = seen;
      // `<html>` does not exist yet when init scripts run, so watch the whole document
      // and keep only `<html>`'s own class changes.
      new MutationObserver((records) => {
        for (const record of records) {
          if (record.target === document.documentElement) seen.push(document.documentElement.className);
        }
      }).observe(document, { attributes: true, attributeFilter: ['class'], subtree: true });
    });
  });

  // The shell is the user's own preference, so switching it never reaches the shared admin.
  test.use({ asThrowawayUser: true });

  test('switches UIv1 -> UIv2 -> UIv3 -> UIv1 and reloads each without a flash of the wrong shell', async ({
    page,
  }) => {
    await page.goto('/settings?tab=appearance');
    const shells = page.getByRole('radiogroup', { name: 'App shell' });
    await expect(shells.getByRole('radio', { name: SHELL_NAMES.v1 })).toHaveAttribute('aria-checked', 'true');
    await expectShellClass(page, 'v1');

    for (const shell of ['v2', 'v3', 'v1'] as const) {
      const saved = page.waitForResponse(
        (response) => response.url().endsWith('/api/auth/me/ui-preference') && response.request().method() === 'PATCH',
      );
      await shells.getByRole('radio', { name: SHELL_NAMES[shell] }).click();
      expect((await saved).ok()).toBeTruthy();

      // The switch applies at once, and the choice is cached for the next page load.
      await expectShellClass(page, shell);
      await expect(shells.getByRole('radio', { name: SHELL_NAMES[shell] })).toHaveAttribute('aria-checked', 'true');
      expect(await cachedShell(page)).toBe(shell);

      await loadWithoutFlash(page, shell);
      await expect(shells.getByRole('radio', { name: SHELL_NAMES[shell] })).toHaveAttribute('aria-checked', 'true');
    }
  });

  test('a ?ui= override wins from the first paint, and the saved preference wins over a stale cache', async ({
    page,
  }) => {
    await api(page, 'patch', '/api/auth/me/ui-preference', { ui_version: 'v3' });
    await page.goto('/settings?tab=appearance');
    await expectShellClass(page, 'v3');
    await expect.poll(() => cachedShell(page)).toBe('v3');

    // The rollback escape hatch: `?ui=v1` pins the classic shell for this load only.
    await loadWithoutFlash(page, 'v1', '/settings?tab=appearance&ui=v1');
    await expect(page.getByText(/override is pinning the shell for this page load/)).toBeVisible();
    await expect(page.getByRole('radio', { name: SHELL_NAMES.v3 })).toHaveAttribute('aria-checked', 'true');

    // A cache that disagrees with the account (the shell was switched on another device)
    // is corrected from the backend once the app loads.
    await page.goto('/settings?tab=appearance');
    await page.evaluate((key) => window.localStorage.setItem(key, 'v2'), UI_VERSION_STORAGE_KEY);
    await page.reload();
    await expectShellClass(page, 'v3');
    await expect.poll(() => cachedShell(page)).toBe('v3');
  });
});

// JAN-225: Settings → Daily Briefing's email window and extra instructions reach the next
// Morning briefing run. The backend's model is a scripted stub (`llm-stub.ts`) that records
// the run's request, where the window shows up as the email summary section.
test.describe('Daily Briefing settings', () => {
  const stub = new LLMStub();
  test.beforeAll(() => stub.start());
  test.afterAll(() => stub.stop());
  // The briefing settings and runs are the user's own; only the backend model is
  // instance-wide, so the admin sets and resets that.
  test.use({ asThrowawayUser: true });
  test.afterEach(async ({ adminRequest }) => {
    await LLMStub.resetBackendModel(adminRequest);
  });

  const promptOf = (request: StubRequest) => request.messages.map((message) => message.content).join('\n');

  /** Runs the Morning briefing again from the briefing page and returns the prompt it sent. */
  const runBriefing = async (page: Page) => {
    const pageLoad = page.waitForEvent('websocket', { predicate: (socket) => socket.url().includes('/ws/briefing') });
    await page.goto('/briefing');
    // The page's own load (today's run, or a fresh one) settles before the refresh.
    const socket = await pageLoad;
    if (!socket.isClosed()) await socket.waitForEvent('close', { timeout: 30_000 });
    const before = stub.requests.length;
    await page.getByRole('button', { name: 'Refresh briefing' }).click();
    await expect(page.getByText('Briefing ready.')).toBeVisible({ timeout: 15_000 });
    const requests = stub.requests.slice(before).filter(isBriefingRequest);
    expect(requests).toHaveLength(1);
    return promptOf(requests[0]);
  };

  /** Saves the settings through the tab and checks they are still there after a reload. */
  const saveSettings = async (page: Page, emailWindow: string, instructions?: string) => {
    await page.goto('/settings?tab=briefing');
    const select = page.getByRole('combobox', { name: 'Email summary window' });
    await select.selectOption({ label: emailWindow });
    const value = await select.inputValue();
    const instructionsField = page.getByLabel('Additional instructions');
    if (instructions !== undefined) await instructionsField.fill(instructions);
    const savedInstructions = await instructionsField.inputValue();
    await page.getByRole('button', { name: 'Save Briefing Settings' }).click();
    await expect(page.getByText('Daily briefing settings saved.')).toBeVisible();

    await page.reload();
    await expect(select).toHaveValue(value);
    await expect(instructionsField).toHaveValue(savedInstructions);
  };

  test('the chosen email window and instructions are used by the next briefing', async ({ page, adminRequest }) => {
    stub.reply(() => ({ chunks: ['Briefing ready.'] }));
    await stub.useAsBackendModel(adminRequest);

    await saveSettings(page, 'Yesterday', 'Lead with blockers.');
    let prompt = await runBriefing(page);
    expect(prompt).toContain('Email Summary (Yesterday):');
    expect(prompt).toContain('Additional instructions from the dashboard briefing widget');
    expect(prompt).toContain('Lead with blockers.');

    await saveSettings(page, 'Last 24 hours');
    prompt = await runBriefing(page);
    expect(prompt).toContain('Email Summary (Last 24 Hours):');
    expect(prompt).not.toContain('Email Summary (Yesterday');
    expect(prompt).toContain('Lead with blockers.');

    await saveSettings(page, 'Today');
    prompt = await runBriefing(page);
    expect(prompt).toContain('Email Summary (Today):');

    // Disabled (with the instructions cleared) keeps both out of the briefing's context.
    await saveSettings(page, 'Disabled', '');
    prompt = await runBriefing(page);
    expect(prompt).not.toContain('Email Summary');
    expect(prompt).not.toContain('Lead with blockers.');
  });
});

/*
 * Triggers tab (JAN-224). The e2e backend runs with the trigger worker disabled, so a run
 * started from the UI stays queued. Finished runs come from the real durable runtime
 * (`run_definition_inline`) with only the model call scripted, so their stored result
 * and step error are exactly what a real run writes.
 */
const RUN_DEFINITIONS_WITH_SCRIPTED_MODEL = `
from janus.agent_task_runner import AgentTaskRun
from janus.trigger_runtime import run_definition_inline
from janus.triggers import TerminalTriggerError

def summarize(db, system_prompt, tools, objective):
    return AgentTaskRun(output="Three newsletters, nothing urgent.\\nThe Acme invoice is due Friday.")

def time_out(db, system_prompt, tools, objective):
    raise RuntimeError("the model endpoint timed out")

def refuse(db, system_prompt, tools, objective):
    raise TerminalTriggerError("the mailbox quota is exceeded")

executors = {"completed": summarize, "failed": time_out, "exhausted": refuse}
for outcome, definition_id in zip(args[0::2], args[1::2]):
    try:
        run_definition_inline(database.SessionLocal, int(definition_id), task_executor=executors[outcome])
    except Exception as error:
        print(f"{outcome}: {type(error).__name__}")
`;

// As a throwaway user (JAN-336), Recent runs and the definition list hold only this test's
// own triggers, never the runs other specs leave on the shared e2e admin.
test.describe('Settings → Triggers (JAN-224)', () => {
  test.use({ asThrowawayUser: true });

  const definitionCard = (page: Page, name: string) =>
    page
      .locator('[data-testid^="trigger-definition-"]')
      .filter({ has: page.getByRole('heading', { name, exact: true }) });

  const triggerEditor = (page: Page) =>
    page.locator('form').filter({ has: page.getByRole('button', { name: 'Save trigger' }) });

  test('creates a manual trigger, runs it now, and disabling it cancels the queued run', async ({ page }) => {
    const name = `E2E manual chore ${Date.now()}`;
    await page.goto('/settings?tab=runtime');
    await expect(page.locator('[data-worker-state="disabled"]')).toContainText(
      'Trigger worker is disabled by configuration'
    );

    await page.getByRole('button', { name: 'New trigger' }).click();
    const editor = triggerEditor(page);
    await editor.getByLabel('Name', { exact: true }).fill(name);
    await editor.getByLabel(/objective/i).fill('Draft a one-line status update.');
    await expect(editor.getByLabel('Activation')).toHaveValue('manual');
    await editor.getByRole('button', { name: 'Save trigger' }).click();
    await expect(editor).toHaveCount(0);

    const card = definitionCard(page, name);
    await expect(card).toContainText('Manual (run on demand)');
    await expect(card).toContainText('Yours');
    await expect(card).toContainText('Tools: core');
    await expect(card.getByRole('checkbox', { name: `Enable ${name}` })).toBeChecked();

    await card.getByRole('button', { name: 'Run now' }).click();
    // The definition's own runs open; the run waits for the (disabled) worker.
    const run = card.getByTestId('trigger-widget');
    await expect(run).toHaveCount(1);
    await expect(run).toHaveAttribute('data-status', 'queued');
    await expect(run).toHaveAttribute('data-kind', 'manual');
    await expect(run.getByTestId('trigger-status-badge')).toHaveText('Queued');

    // Recent runs lists it too, and filtering by the definition keeps only it.
    const history = page.locator('[data-ui-section="trigger_runs"]');
    await history.getByRole('combobox', { name: 'Filter runs by definition' }).selectOption({ label: name });
    await expect(history.getByTestId('trigger-widget')).toHaveCount(1);
    await expect(history.getByTestId('trigger-widget')).toHaveAttribute('data-status', 'queued');
    await expect(history.getByTestId('trigger-widget').getByRole('button', { name, exact: true })).toBeVisible();

    // Disabling the definition cancels its pending run.
    await card.getByRole('checkbox', { name: `Enable ${name}` }).click();
    await expect(card.getByText('Disabled', { exact: true })).toBeVisible();
    await expect(history.getByTestId('trigger-widget')).toHaveAttribute('data-status', 'cancelled');
    await expect(history.getByTestId('trigger-status-badge')).toHaveText('Cancelled');

    // Deleting asks first; Cancel keeps the trigger.
    await card.getByRole('button', { name: `Delete ${name}` }).click();
    await answerConfirm(page, 'Delete trigger?', 'Cancel');
    await expect(card).toHaveCount(1);
    await card.getByRole('button', { name: `Delete ${name}` }).click();
    await answerConfirm(page, 'Delete trigger?', 'Delete');
    await expect(card).toHaveCount(0);

    // A trigger created next starts with no runs: it never inherits the deleted one's
    // history, even when the database hands it the same id.
    const successor = `E2E successor ${Date.now()}`;
    const created = await api(page, 'post', '/api/runtime/definitions/', {
      name: successor, objective: 'Say hello.', tool_ids: ['core'], kind: 'manual', timezone: 'UTC', enabled: true,
    });
    const successorId = (await created.json()).id;
    await page.reload();
    await definitionCard(page, successor).getByRole('button', { name: 'Runs' }).click();
    await expect(definitionCard(page, successor).getByText('No runs yet.')).toBeVisible();
    const runs = await (await page.request.get(`/api/runtime/definitions/${successorId}/runs`)).json();
    expect(runs).toEqual([]);
    await api(page, 'delete', `/api/runtime/definitions/${successorId}`);
  });

  test('creates a recurring schedule trigger with its next run, which disabling clears', async ({ page }) => {
    const name = `E2E weekday sweep ${Date.now()}`;
    await page.goto('/settings?tab=runtime');
    await page.getByRole('button', { name: 'New trigger' }).click();
    const editor = triggerEditor(page);
    await editor.getByLabel('Name', { exact: true }).fill(name);
    await editor.getByLabel(/objective/i).fill('Summarize what changed overnight.');
    await editor.getByLabel('Activation').selectOption('schedule');
    await expect(editor.getByLabel('Repeat')).toHaveValue('daily');
    await editor.getByLabel('Time', { exact: true }).fill('06:45');
    await editor.getByLabel('Timezone', { exact: true }).fill('Europe/Berlin');
    await editor.getByRole('checkbox', { name: 'Mon', exact: true }).check();
    await editor.getByRole('checkbox', { name: 'Fri', exact: true }).check();
    await editor.getByRole('button', { name: 'Save trigger' }).click();
    await expect(editor).toHaveCount(0);

    const card = definitionCard(page, name);
    await expect(card).toContainText('Daily at 06:45 (Mon, Fri, Europe/Berlin)');
    await expect(card).toContainText('next run');

    // An interval under the backend's 5-minute floor is refused with its reason.
    await card.getByRole('button', { name: 'Edit' }).click();
    const edit = triggerEditor(page);
    await edit.getByLabel('Repeat').selectOption('interval');
    const interval = edit.getByLabel('Interval (minutes, 5–10080)');
    await interval.fill('2');
    // Skip the browser's own min check so the request reaches the backend.
    await interval.evaluate((input: HTMLInputElement) => input.removeAttribute('min'));
    await edit.getByRole('button', { name: 'Save trigger' }).click();
    await expect(edit.locator('p.text-red-500')).toBeVisible();
    await expect(edit).toBeVisible();
    await interval.fill('90');
    await edit.getByRole('button', { name: 'Save trigger' }).click();
    await expect(edit).toHaveCount(0);
    await expect(card).toContainText('Every 90 minutes');
    await expect(card).toContainText('next run');

    await card.getByRole('checkbox', { name: `Enable ${name}` }).click();
    await expect(card.getByText('Disabled', { exact: true })).toBeVisible();
    await expect(card).not.toContainText('next run');

    await page.reload();
    await expect(definitionCard(page, name)).toContainText('Every 90 minutes');
    await expect(definitionCard(page, name)).not.toContainText('next run');
    await definitionCard(page, name).getByRole('button', { name: `Delete ${name}` }).click();
    await answerConfirm(page, 'Delete trigger?', 'Delete');
    await page.reload();
    await expect(definitionCard(page, name)).toHaveCount(0);
  });

  test('shows a completed run with its result and failed or exhausted runs with their step error', async ({
    page,
    account,
  }) => {
    const stamp = Date.now();
    const names = {
      completed: `E2E inbox digest ${stamp}`,
      failed: `E2E flaky summary ${stamp}`,
      exhausted: `E2E quota check ${stamp}`,
    };
    const outcomes = Object.keys(names) as (keyof typeof names)[];
    const ids = {} as Record<keyof typeof names, number>;
    await page.goto('/settings?tab=runtime');
    for (const outcome of outcomes) {
      const created = await api(page, 'post', '/api/runtime/definitions/', {
        name: names[outcome],
        objective: 'Summarize new mail.',
        tool_ids: ['core'],
        kind: 'manual',
        timezone: 'UTC',
        enabled: true,
      });
      ids[outcome] = (await created.json()).id;
    }
    await runBackendPython(
      RUN_DEFINITIONS_WITH_SCRIPTED_MODEL,
      outcomes.flatMap((outcome) => [outcome, ids[outcome]]),
      { as: account.username },
    );

    await page.reload();
    const history = page.locator('[data-ui-section="trigger_runs"]');
    const runOf = (name: string) =>
      history.getByTestId('trigger-widget').filter({ has: page.getByRole('button', { name, exact: true }) });

    const completed = runOf(names.completed);
    await expect(completed).toHaveAttribute('data-status', 'completed');
    await expect(completed.getByTestId('trigger-status-badge')).toHaveText('Completed');
    await expect(completed.getByTestId('trigger-result')).toContainText('Three newsletters, nothing urgent.');
    await expect(completed.getByTestId('trigger-result')).toContainText('The Acme invoice is due Friday.');
    await expect(completed.getByTestId('trigger-last-error')).toHaveCount(0);
    await expect(completed).toContainText('1 attempt');

    // A retryable failure keeps its step error; with no backoff it is retried on the
    // worker's next poll, so there is no "retry after" time to show.
    const failed = runOf(names.failed);
    await expect(failed).toHaveAttribute('data-status', 'failed');
    await expect(failed.getByTestId('trigger-status-badge')).toHaveText('Failed');
    await expect(failed.getByTestId('trigger-last-error')).toContainText('the model endpoint timed out');
    await expect(failed).toContainText('1 attempt');
    await expect(failed.getByTestId('trigger-result')).toHaveCount(0);

    // A terminal failure is exhausted at once, with no retry.
    const exhausted = runOf(names.exhausted);
    await expect(exhausted).toHaveAttribute('data-status', 'exhausted');
    await expect(exhausted.getByTestId('trigger-status-badge')).toHaveText('Exhausted');
    await expect(exhausted.getByTestId('trigger-last-error')).toContainText('the mailbox quota is exceeded');
    await expect(exhausted).not.toContainText('retry after');

    // The status filter narrows the history.
    await history.getByRole('combobox', { name: 'Filter runs by status' }).selectOption('exhausted');
    await expect(runOf(names.exhausted)).toHaveCount(1);
    await expect(runOf(names.completed)).toHaveCount(0);
    await expect(runOf(names.failed)).toHaveCount(0);

    // A run's name jumps to its definition, whose own run list shows the same outcome.
    await history.getByRole('combobox', { name: 'Filter runs by status' }).selectOption('completed');
    await runOf(names.completed).getByRole('button', { name: names.completed, exact: true }).click();
    const definition = definitionCard(page, names.completed);
    await expect(definition).toHaveAttribute('data-highlighted', 'true');
    await definition.getByRole('button', { name: 'Runs' }).click();
    await expect(definition.getByTestId('trigger-result')).toContainText('Three newsletters, nothing urgent.');
  });
});
