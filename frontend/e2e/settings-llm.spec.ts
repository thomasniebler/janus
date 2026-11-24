import { type Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { FAKE_AZURE_DENIED_KEY, FAKE_UNKNOWN_MODEL, FakeIntegrations } from './fakeIntegrations';
import { api } from './helpers';

/**
 * Settings -> AI & System, AI Configuration (JAN-222): provider switching, draft
 * credentials, the per-provider model dropdown fed by `/api/planner/llm-models`, and
 * "Test Selected Backend", against a loopback fake of every provider API
 * (`fakeIntegrations.ts`), so the real backend makes its real calls and nothing reaches
 * OpenAI, Anthropic, OpenRouter, Google, Ollama, or GitHub Models. Azure OpenAI only
 * accepts https endpoints, so its flow covers the form, persistence, and the endpoint
 * check that fails before any request (JAN-340).
 */
const SAVED_OK = 'AI backend settings saved.';
const TEST_OK = 'Successfully connected to LLM provider.';

const provider = (page: Page, label: string) => page.getByRole('button', { name: new RegExp(`^${label} Use`) });
/** The card's FeedbackBanner: `status` on success, `alert` on failure. */
const succeeded = (page: Page, message: string) => page.getByRole('status').filter({ hasText: message });
const failed = (page: Page) => page.getByRole('alert');

/** Opens the model dropdown and picks an option; the list comes from the fake provider. */
const pickModel = async (page: Page, label: string, option: RegExp) => {
  const combobox = page.getByRole('combobox', { name: label });
  await expect(combobox).toBeVisible();
  await combobox.click();
  await page.getByRole('option', { name: option }).click();
  await expect(page.getByRole('listbox')).toHaveCount(0);
};

test.describe.serial('AI backend configuration', () => {
  const fake = new FakeIntegrations();
  let initial: Record<string, unknown>;

  test.beforeAll(async () => {
    await fake.start();
  });

  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    if (!initial) initial = await (await page.request.get('/api/planner/settings')).json();
  });

  test.afterAll(async ({ browser }) => {
    await fake.stop();
    // Leave the shared backend on its default (unreachable) Ollama with no stored keys,
    // as the briefing spec expects.
    const page = await browser.newPage();
    await page.goto('/');
    await api(page, 'patch', '/api/planner/settings', {
      llm_provider: initial.llm_provider,
      ollama_model: initial.ollama_model,
      ollama_base_url: initial.ollama_base_url,
      openai_model: initial.openai_model,
      openai_base_url: '',
      openrouter_model: initial.openrouter_model,
      openrouter_base_url: initial.openrouter_base_url,
      gemini_model: initial.gemini_model,
      anthropic_model: initial.anthropic_model,
      anthropic_base_url: '',
      ollama_api_key: '',
      openai_api_key: '',
      openrouter_api_key: '',
      gemini_api_key: '',
      anthropic_api_key: '',
      github_models_api_key: '',
      github_models_base_url: '',
      azure_openai_endpoint: '',
      azure_openai_deployment: '',
      azure_openai_api_key: '',
      azure_openai_auth_mode: 'entra',
    });
    await page.close();
  });

  test('lists Ollama models from the draft base URL, tests the connection, and saves', async ({ page }) => {
    await page.goto('/settings?tab=system');
    await expect(page.getByRole('heading', { name: 'AI Configuration' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Ollama / Llama settings' })).toBeVisible();

    await page.getByLabel('Ollama Base URL').fill(fake.url);
    await expect(page.getByText('2 models available', { exact: false })).toBeVisible();
    await pickModel(page, 'Ollama Model', /qwen-e2e:7b/);

    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(succeeded(page, TEST_OK)).toBeVisible();
    const chat = fake.requestsTo('/api/chat').at(-1)!;
    expect(JSON.parse(chat.body).model).toBe('qwen-e2e:7b');

    await page.getByRole('button', { name: 'Save Backend Settings' }).click();
    await expect(succeeded(page, SAVED_OK)).toBeVisible();
    await page.reload();
    await expect(page.getByLabel('Ollama Base URL')).toHaveValue(fake.url);
    await expect(page.getByRole('combobox', { name: 'Ollama Model' })).toHaveValue('qwen-e2e:7b');
  });

  test('a provider that refuses the model fails the connection test with its reason', async ({ page }) => {
    await page.goto('/settings?tab=system');
    const model = page.getByRole('combobox', { name: 'Ollama Model' });
    await model.fill(FAKE_UNKNOWN_MODEL);
    await model.press('Escape');

    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(failed(page)).toContainText('LLM connection failed');
    await expect(failed(page)).toContainText(FAKE_UNKNOWN_MODEL);
  });

  test('switches to OpenAI, needs a key for its models, then saves the key encrypted and write-only', async ({ page }) => {
    const key = 'sk-e2e-openai-secret';
    await page.goto('/settings?tab=system');
    await provider(page, 'OpenAI').click();
    await expect(page.getByRole('heading', { name: 'OpenAI settings' })).toBeVisible();
    await expect(page.getByText('No OpenAI API key has been stored yet.')).toBeVisible();
    // Without a key, OpenAI cannot list models: the field falls back to free text.
    await expect(page.getByText('Enter an OpenAI API key to load the model list.')).toBeVisible();

    await page.getByLabel('OpenAI Base URL').fill(`${fake.url}/v1`);
    await page.getByLabel('OpenAI API Key').fill(key);
    // Chat models only: the embedding model the provider also lists is left out.
    await expect(page.getByText('2 models available', { exact: false })).toBeVisible();
    await pickModel(page, 'OpenAI Model', /gpt-e2e-large/);
    expect(fake.requestsTo('/v1/models').at(-1)!.headers.authorization).toBe(`Bearer ${key}`);

    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(succeeded(page, TEST_OK)).toBeVisible();
    const completion = fake.requestsTo('/v1/chat/completions').at(-1)!;
    expect(completion.headers.authorization).toBe(`Bearer ${key}`);
    expect(JSON.parse(completion.body).model).toBe('gpt-e2e-large');

    await page.getByRole('button', { name: 'Save Backend Settings' }).click();
    await expect(succeeded(page, SAVED_OK)).toBeVisible();
    await expect(page.getByText('An OpenAI API key is already stored.')).toBeVisible();

    // The key never comes back: not in the settings read-back, not in the form.
    const readBack = await page.request.get('/api/planner/settings');
    expect(await readBack.text()).not.toContain(key);
    expect(await readBack.json()).toMatchObject({
      llm_provider: 'openai',
      openai_model: 'gpt-e2e-large',
      openai_api_key_configured: true,
    });
    await page.reload();
    await expect(page.getByRole('heading', { name: 'OpenAI settings' })).toBeVisible();
    await expect(page.getByLabel('OpenAI API Key')).toHaveValue('');
    await expect(page.getByLabel('OpenAI API Key')).toHaveAttribute('placeholder', /Stored key is configured/);
    // The stored key still lists models from the base URL it was saved with.
    await expect(page.getByText('2 models available', { exact: false })).toBeVisible();
  });

  test('never sends the stored key to a draft base URL', async ({ page }) => {
    await page.goto('/settings?tab=system');
    await expect(page.getByRole('heading', { name: 'OpenAI settings' })).toBeVisible();
    const before = fake.requests.length;

    await page.getByLabel('OpenAI Base URL').fill(`${fake.url}/elsewhere/v1`);
    await expect(page.getByText('Enter the OpenAI API key to list models from a custom base URL.')).toBeVisible();
    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(failed(page)).toHaveText(
      'Enter the API key to test this base URL: the stored key is only sent to the base URL it was saved with.'
    );
    expect(fake.requests.slice(before).filter((request) => request.path.startsWith('/elsewhere'))).toEqual([]);
  });

  test('picks an Anthropic model with a draft key and tests it', async ({ page }) => {
    const key = 'sk-ant-e2e-secret';
    await page.goto('/settings?tab=system');
    await provider(page, 'Anthropic').click();
    await expect(page.getByRole('heading', { name: 'Anthropic settings' })).toBeVisible();

    await page.getByLabel('Anthropic Base URL').fill(fake.url);
    await page.getByLabel('Anthropic API Key').fill(key);
    await pickModel(page, 'Anthropic Model', /Claude E2E Opus/);
    await expect(page.getByRole('combobox', { name: 'Anthropic Model' })).toHaveValue('claude-e2e-opus');

    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(succeeded(page, TEST_OK)).toBeVisible();
    const message = fake.requestsTo('/v1/messages').at(-1)!;
    expect(message.headers['x-api-key']).toBe(key);
    expect(JSON.parse(message.body).model).toBe('claude-e2e-opus');
  });

  test('picks an OpenRouter model with a draft key and tests it', async ({ page }) => {
    // OpenRouter lists models without a key, so its stored base URL points at the fake
    // first: selecting the provider must never call the real openrouter.ai.
    await api(page, 'patch', '/api/planner/settings', { openrouter_base_url: `${fake.url}/openrouter` });
    await page.goto('/settings?tab=system');
    await provider(page, 'OpenRouter').click();
    await expect(page.getByRole('heading', { name: 'OpenRouter settings' })).toBeVisible();
    await expect(page.getByLabel('OpenRouter Base URL')).toHaveValue(`${fake.url}/openrouter`);

    await page.getByLabel('OpenRouter API Key').fill('sk-or-e2e-secret');
    await pickModel(page, 'OpenRouter Model', /E2E Router Model/);

    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(succeeded(page, TEST_OK)).toBeVisible();
    expect(JSON.parse(fake.requestsTo('/openrouter/chat/completions').at(-1)!.body).model).toBe('e2e/router-model');
  });

  test('Gemini without a key falls back to a typed model name', async ({ page }) => {
    await page.goto('/settings?tab=system');
    await provider(page, 'Gemini').click();
    await expect(page.getByRole('heading', { name: 'Gemini settings' })).toBeVisible();
    await expect(page.getByText('No Gemini API key has been stored yet.')).toBeVisible();
    await expect(page.getByText('Could not load model list — enter a model name manually.')).toBeVisible();
    await expect(page.getByText('Enter a Gemini API key to load the model list.')).toBeVisible();

    await page.getByLabel('Gemini Model').fill('gemini-e2e-flash');
    await page.getByRole('button', { name: 'Save Backend Settings' }).click();
    await expect(succeeded(page, SAVED_OK)).toBeVisible();
    expect(await (await page.request.get('/api/planner/settings')).json()).toMatchObject({
      llm_provider: 'gemini',
      gemini_model: 'gemini-e2e-flash',
      gemini_api_key_configured: false,
    });
  });

  test('picks a GitHub Models model with a token, tests it, and saves the token write-only', async ({ page }) => {
    const token = 'github_pat_e2e_secret';
    // GitHub Models needs a token to list, but point its stored base URL at the fake first
    // so a typed token can never reach the real models.github.ai.
    await api(page, 'patch', '/api/planner/settings', { github_models_base_url: `${fake.url}/github/inference` });
    await page.goto('/settings?tab=system');
    await provider(page, 'GitHub Models').click();
    await expect(page.getByRole('heading', { name: 'GitHub Models settings' })).toBeVisible();
    await expect(page.getByText('No GitHub token for GitHub Models has been stored yet.')).toBeVisible();

    await page.getByLabel('GitHub Token').fill(token);
    // Text models only: the catalog's embedding model is left out.
    await expect(page.getByText('2 models available', { exact: false })).toBeVisible();
    await pickModel(page, 'GitHub Models Model', /Meta Llama E2E/);
    expect(fake.requestsTo('/github/catalog/models').at(-1)!.headers.authorization).toBe(`Bearer ${token}`);

    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(succeeded(page, TEST_OK)).toBeVisible();
    const completion = fake.requestsTo('/github/inference/chat/completions').at(-1)!;
    expect(completion.headers.authorization).toBe(`Bearer ${token}`);
    expect(JSON.parse(completion.body).model).toBe('meta/llama-e2e');

    await page.getByRole('button', { name: 'Save Backend Settings' }).click();
    await expect(succeeded(page, SAVED_OK)).toBeVisible();
    await expect(page.getByText('A GitHub token for GitHub Models is already stored.')).toBeVisible();
    const readBack = await page.request.get('/api/planner/settings');
    expect(await readBack.text()).not.toContain(token);
    expect(await readBack.json()).toMatchObject({
      llm_provider: 'github_models',
      github_models_model: 'meta/llama-e2e',
      github_models_api_key_configured: true,
    });
  });

  test('configures Azure OpenAI: Entra ID by default, then a deployment listed with a write-only API key', async ({
    page,
  }) => {
    const key = 'azure-e2e-secret';
    await page.goto('/settings?tab=system');
    await provider(page, 'Azure OpenAI').click();
    await expect(page.getByRole('heading', { name: 'Azure OpenAI settings' })).toBeVisible();
    // Entra ID is the default and stores no secret, so there is no key field.
    await expect(page.getByLabel('Auth Mode')).toHaveValue('entra');
    await expect(page.getByText(/Entra ID auth stores no secret/)).toBeVisible();
    await expect(page.getByLabel('Azure OpenAI API Key')).toHaveCount(0);

    // Entra ID settings save and survive a reload. The fake Foundry host cannot list deployments
    // (no Azure sign-in in CI, no such host anyway), so the field falls back to typing the name.
    await page.getByLabel('Azure OpenAI Endpoint').fill('https://e2e-foundry.cognitiveservices.azure.com');
    await expect(page.getByText('Could not load model list — enter a model name manually.')).toBeVisible({ timeout: 15_000 });
    await page.getByLabel('Deployment Name').fill('Kimi-K2.6');
    await page.getByRole('button', { name: 'Save Backend Settings' }).click();
    await expect(succeeded(page, SAVED_OK)).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Azure OpenAI settings' })).toBeVisible();
    await expect(page.getByLabel('Auth Mode')).toHaveValue('entra');
    await expect(page.getByLabel('Azure OpenAI Endpoint')).toHaveValue('https://e2e-foundry.cognitiveservices.azure.com');
    await expect(page.getByLabel('Deployment Name')).toHaveValue('Kimi-K2.6');
    expect(await (await page.request.get('/api/planner/settings')).json()).toMatchObject({
      llm_provider: 'azure_openai',
      llm_model: 'Kimi-K2.6',
      azure_openai_auth_mode: 'entra',
      azure_openai_api_key_configured: false,
    });

    // API key mode against the plain-http loopback fake (admitted by the e2e test seam only).
    await page.getByLabel('Auth Mode').selectOption('api_key');
    await expect(page.getByText('No Azure OpenAI API key has been stored yet.')).toBeVisible();
    await page.getByLabel('Azure OpenAI API Key').fill(FAKE_AZURE_DENIED_KEY);
    // A pasted portal URL with a path is refused with the root to use, before any request.
    await page.getByLabel('Azure OpenAI Endpoint').fill(`${fake.url}/openai/v1/`);
    await expect(page.getByText(`for example ${fake.url}.`)).toBeVisible();
    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(failed(page)).toContainText(`for example ${fake.url}.`);

    // A key that may not read deployments falls back to typing the name (JAN-349).
    await page.getByLabel('Azure OpenAI Endpoint').fill(fake.url);
    await expect(page.getByText(/PermissionDenied/)).toBeVisible();
    expect(fake.requestsTo('/openai/deployments').at(-1)!.headers['api-key']).toBe(FAKE_AZURE_DENIED_KEY);

    // With a key that may, the deployments are listed as "deployment (model)".
    await page.getByLabel('Azure OpenAI API Key').fill(key);
    await expect(page.getByText('2 models available', { exact: false })).toBeVisible();
    await pickModel(page, 'Deployment Name', /Kimi-K2\.6 \(Kimi-K2\.6\)/);
    await expect(page.getByRole('combobox', { name: 'Deployment Name' })).toHaveValue('Kimi-K2.6');
    const listing = fake.requestsTo('/openai/deployments').at(-1)!;
    expect(listing.headers['api-key']).toBe(key);
    expect(listing.query.get('api-version')).toBe('2023-03-15-preview');

    await page.getByRole('button', { name: 'Test Selected Backend' }).click();
    await expect(succeeded(page, TEST_OK)).toBeVisible();
    // The default api-version (preview) uses the v1 endpoint; Kimi stays on Chat Completions there (JAN-356).
    const chat = fake.requestsTo('/openai/v1/chat/completions').at(-1)!;
    expect(chat.headers['api-key']).toBe(key);
    expect(chat.query.get('api-version')).toBe('preview');
    expect(JSON.parse(chat.body).model).toBe('Kimi-K2.6');

    await page.getByRole('button', { name: 'Save Backend Settings' }).click();
    await expect(succeeded(page, SAVED_OK)).toBeVisible();
    await expect(page.getByText('An Azure OpenAI API key is already stored.')).toBeVisible();
    const readBack = await page.request.get('/api/planner/settings');
    expect(await readBack.text()).not.toContain(key);
    expect(await readBack.json()).toMatchObject({
      llm_provider: 'azure_openai',
      llm_model: 'Kimi-K2.6',
      azure_openai_endpoint: fake.url,
      azure_openai_auth_mode: 'api_key',
      azure_openai_api_key_configured: true,
    });
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Azure OpenAI settings' })).toBeVisible();
    await expect(page.getByLabel('API Version')).toHaveValue('preview');
    // The stored key lists from the endpoint it was saved with.
    await expect(page.getByRole('combobox', { name: 'Deployment Name' })).toHaveValue('Kimi-K2.6');
  });
});
