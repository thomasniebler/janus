import { type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { answerConfirm, api, confirmDialog, resetUtilityModel, setCloudUtilityModel } from './helpers';

const CARD_NAME = 'Model-based prompt-injection check';
const CLOUD_TITLE = 'Send suspicious outside text to a cloud model?';
const DEFAULT_OLLAMA_URL = 'http://localhost:11434';

const card = (page: Page) => page.getByTestId('injection-classifier-card');
const toggle = (page: Page) => card(page).getByRole('checkbox', { name: CARD_NAME });
const classifierStatus = async (page: Page) => (await page.request.get('/api/tools/injection-classifier')).json();

/** Settings -> Tools & Connections, Model-based prompt-injection check card (JAN-289). */
test.describe('Model-based prompt-injection check', () => {
  test.afterEach(async ({ page }) => {
    // Leave the check off with the default budget, and the utility model local, for every other test.
    await api(page, 'put', '/api/tools/injection-classifier', { enabled: false, monthly_token_budget: 200000 });
    await resetUtilityModel(page);
    await api(page, 'patch', '/api/planner/settings', { ollama_base_url: DEFAULT_OLLAMA_URL });
  });

  test('switches on with a local model without asking, saves the budget, and keeps both across a reload', async ({
    page,
  }) => {
    await page.goto('/settings?tab=tools');
    await expect(card(page).getByRole('heading', { name: CARD_NAME })).toBeVisible();
    await expect(toggle(page)).not.toBeChecked();
    await expect(card(page).getByText('Off', { exact: true })).toBeVisible();
    await expect(card(page).getByText(/is a cloud model/)).toHaveCount(0);
    await expect(card(page).getByText(/of 200,000\s+tokens/)).toBeVisible();

    // A local utility model needs no confirmation.
    // The switch is controlled: it flips once the save answers, so click and wait.
    await toggle(page).click();
    await expect(toggle(page)).toBeChecked();
    await expect(confirmDialog(page, CLOUD_TITLE)).toHaveCount(0);
    await expect(card(page).getByText('On', { exact: true })).toBeVisible();

    // An invalid budget cannot be saved; a valid one is.
    const budget = card(page).getByLabel('Monthly token budget');
    const save = card(page).getByRole('button', { name: 'Save' });
    await budget.fill('');
    await expect(card(page).getByText('Enter a whole number of tokens.')).toBeVisible();
    await expect(save).toBeDisabled();
    await budget.fill('5000');
    await save.click();
    await expect(card(page).getByText(/of 5,000\s+tokens/)).toBeVisible();

    await page.reload();
    await expect(toggle(page)).toBeChecked();
    await expect(card(page).getByLabel('Monthly token budget')).toHaveValue('5000');
    expect(await classifierStatus(page)).toMatchObject({ enabled: true, monthly_token_budget: 5000, is_cloud: false });

    // Switching it off again is saved too.
    await toggle(page).click();
    await expect(toggle(page)).not.toBeChecked();
    await expect(card(page).getByText('Off', { exact: true })).toBeVisible();
    await page.reload();
    await expect(toggle(page)).not.toBeChecked();
  });

  test('warns about a cloud utility model and asks before switching on', async ({ page }) => {
    await setCloudUtilityModel(page);
    await page.goto('/settings?tab=tools');
    await expect(card(page).getByText(/openai\/gpt-4o-mini is a cloud model/)).toBeVisible();

    // Cancel keeps it off.
    await toggle(page).click();
    await answerConfirm(page, CLOUD_TITLE, 'Cancel');
    await expect(toggle(page)).not.toBeChecked();
    expect((await classifierStatus(page)).enabled).toBe(false);

    // Turn on enables it.
    await toggle(page).click();
    await answerConfirm(page, CLOUD_TITLE, 'Turn on');
    await expect(toggle(page)).toBeChecked();
    expect((await classifierStatus(page)).enabled).toBe(true);
  });

  test('treats hosted Ollama as a cloud model', async ({ page }) => {
    await api(page, 'patch', '/api/planner/settings', {
      utility_llm_provider: 'ollama',
      ollama_base_url: 'https://ollama.com/api',
    });
    await page.goto('/settings?tab=tools');
    await expect(card(page).getByText(/ollama\/\S+ is a cloud model/)).toBeVisible();
    await toggle(page).click();
    await answerConfirm(page, CLOUD_TITLE, 'Cancel');
    await expect(toggle(page)).not.toBeChecked();
    expect(await classifierStatus(page)).toMatchObject({ enabled: false, is_cloud: true });
  });
});
