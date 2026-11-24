import { expect, test } from './fixtures';
import { api } from './helpers';

/** Self-evolution settings card on Settings -> Tools & Connections (JAN-320). */
test.describe('Self-evolution settings', () => {
  test.afterEach(async ({ page }) => {
    // Leave the loop off and unconfigured for every other test.
    await api(page, 'patch', '/api/self-evolution/settings', {
      enabled: false,
      routine_trigger_id: '',
      routine_token: '',
    });
  });

  test('switches the loop on with a routine trigger id and token, keeps it across a reload, and rejects a URL', async ({
    page,
  }) => {
    await page.goto('/settings?tab=tools');
    const card = page.getByTestId('self-evolution-card');
    await expect(card.getByRole('heading', { name: 'Self-evolution' })).toBeVisible();
    await expect(card.getByText('Off', { exact: true })).toBeVisible();
    await expect(card.getByText('Feature merges: 0 / 3')).toBeVisible();

    const toggle = card.getByLabel('Run the self-evolution loop autonomously (no human review)');
    const save = card.getByRole('button', { name: 'Save Settings' });
    await expect(toggle).not.toBeChecked();
    await expect(save).toBeDisabled();

    // A URL instead of a trig_ id is refused by the backend and nothing is saved.
    await card.getByLabel('Claude Code Routine trigger id').fill('https://evil.example.com/fire');
    await save.click();
    await expect(card.getByText(/routine_trigger_id must look like trig_/)).toBeVisible();

    await card.getByLabel('Claude Code Routine trigger id').fill('trig_01E2eRoutine');
    await card.getByLabel('Routine API token').fill('sk-ant-oat01-e2e-token');
    await toggle.check();
    await save.click();
    await expect(card.getByText('Self-evolution settings saved.')).toBeVisible();
    await expect(card.getByText('On', { exact: true })).toBeVisible();

    await page.reload();
    const reloaded = page.getByTestId('self-evolution-card');
    await expect(reloaded.getByLabel('Run the self-evolution loop autonomously (no human review)')).toBeChecked();
    await expect(reloaded.getByLabel('Claude Code Routine trigger id')).toHaveValue('trig_01E2eRoutine');
    await expect(reloaded.getByLabel('Routine API token')).toHaveValue('');
    await expect(reloaded.getByLabel('Routine API token')).toHaveAttribute(
      'placeholder',
      'Token configured — paste to replace',
    );
    // The token never comes back from the API.
    const settings = await (await page.request.get('/api/self-evolution/settings')).json();
    expect(settings).toMatchObject({ enabled: true, routine_trigger_id: 'trig_01E2eRoutine', routine_token_configured: true });
    expect(JSON.stringify(settings)).not.toContain('sk-ant-oat01-e2e-token');

    // Switching it off again is saved too.
    await reloaded.getByLabel('Run the self-evolution loop autonomously (no human review)').uncheck();
    await reloaded.getByRole('button', { name: 'Save Settings' }).click();
    await expect(reloaded.getByText('Self-evolution settings saved.')).toBeVisible();
    await expect(reloaded.getByText('Off', { exact: true })).toBeVisible();
  });
});
