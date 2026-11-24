import { describe, expect, it } from 'vitest';
import type { PlannerSettings } from '../types';
import { buildGitHubModelsAzureDraft, secretStatusMessage, toGitHubModelsAzurePayload } from './llmProviderDrafts';

const settings = {
  ollama_api_key_configured: false,
  openai_api_key_configured: true,
  openrouter_api_key_configured: false,
  gemini_api_key_configured: false,
  anthropic_api_key_configured: false,
  github_models_model: 'openai/gpt-4.1-mini',
  github_models_base_url: null,
  github_models_api_key_configured: true,
  azure_openai_endpoint: 'https://example.openai.azure.com',
  azure_openai_deployment: null,
  azure_openai_api_version: '2024-10-21',
  azure_openai_auth_mode: 'entra',
  azure_openai_api_key_configured: false,
} as PlannerSettings;

describe('llmProviderDrafts (JAN-340)', () => {
  it('round-trips the GitHub Models and Azure fields, keeping stored secrets when left empty', () => {
    const draft = buildGitHubModelsAzureDraft(settings);
    expect(draft.azure_openai_deployment).toBe('');
    expect(toGitHubModelsAzurePayload({ ...draft, azure_openai_deployment: ' Kimi-K2.6 ' })).toEqual({
      github_models_model: 'openai/gpt-4.1-mini',
      github_models_base_url: '',
      github_models_api_key: undefined,
      azure_openai_endpoint: 'https://example.openai.azure.com',
      azure_openai_deployment: 'Kimi-K2.6',
      azure_openai_api_version: '2024-10-21',
      azure_openai_auth_mode: 'entra',
      azure_openai_api_key: undefined,
    });
  });

  it('describes the stored secret per provider, and Entra ID as secret-free', () => {
    expect(secretStatusMessage('openai', settings, 'entra')).toBe('An OpenAI API key is already stored.');
    expect(secretStatusMessage('github_models', settings, 'entra')).toBe('A GitHub token for GitHub Models is already stored.');
    expect(secretStatusMessage('azure_openai', settings, 'entra')).toMatch(/entra id auth stores no secret/i);
    expect(secretStatusMessage('azure_openai', settings, 'api_key')).toBe('No Azure OpenAI API key has been stored yet.');
  });
});
