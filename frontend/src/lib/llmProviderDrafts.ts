import type { AzureOpenAIAuthMode, LLMProviderId } from './llmProviders';
import type { PlannerSettings, PlannerSettingsUpdate } from '../types';

/** The editable GitHub Models and Azure OpenAI fields of the AI settings form (JAN-340). */
export type GitHubModelsAzureDraft = {
  github_models_model: string;
  github_models_base_url: string;
  github_models_api_key: string;
  azure_openai_endpoint: string;
  azure_openai_deployment: string;
  azure_openai_api_version: string;
  azure_openai_auth_mode: AzureOpenAIAuthMode;
  azure_openai_api_key: string;
};

/** Seeds the form from stored settings; secrets start empty ("keep the stored one"). */
export const buildGitHubModelsAzureDraft = (settings: PlannerSettings): GitHubModelsAzureDraft => ({
  github_models_model: settings.github_models_model,
  github_models_base_url: settings.github_models_base_url || '',
  github_models_api_key: '',
  azure_openai_endpoint: settings.azure_openai_endpoint || '',
  azure_openai_deployment: settings.azure_openai_deployment || '',
  azure_openai_api_version: settings.azure_openai_api_version,
  azure_openai_auth_mode: settings.azure_openai_auth_mode,
  azure_openai_api_key: '',
});

/** Trims the draft for saving or testing; an empty secret is left out so the stored one is kept. */
export const toGitHubModelsAzurePayload = (draft: GitHubModelsAzureDraft): PlannerSettingsUpdate => ({
  github_models_model: draft.github_models_model.trim(),
  github_models_base_url: draft.github_models_base_url.trim(),
  github_models_api_key: draft.github_models_api_key.trim() || undefined,
  azure_openai_endpoint: draft.azure_openai_endpoint.trim(),
  azure_openai_deployment: draft.azure_openai_deployment.trim(),
  azure_openai_api_version: draft.azure_openai_api_version.trim(),
  azure_openai_auth_mode: draft.azure_openai_auth_mode,
  azure_openai_api_key: draft.azure_openai_api_key.trim() || undefined,
});

/** The "Secret status" line for the selected provider: whether its key or token is stored. */
export const secretStatusMessage = (
  provider: LLMProviderId,
  settings: PlannerSettings,
  azureAuthMode: AzureOpenAIAuthMode
): string => {
  const status: Record<LLMProviderId, [boolean, string, string]> = {
    ollama: [
      settings.ollama_api_key_configured,
      'An Ollama API key is already stored.',
      'No Ollama API key is stored. Local runtimes do not need one, but Ollama Cloud does.',
    ],
    openai: [settings.openai_api_key_configured, 'An OpenAI API key is already stored.', 'No OpenAI API key has been stored yet.'],
    openrouter: [
      settings.openrouter_api_key_configured,
      'An OpenRouter API key is already stored.',
      'No OpenRouter API key has been stored yet.',
    ],
    gemini: [settings.gemini_api_key_configured, 'A Gemini API key is already stored.', 'No Gemini API key has been stored yet.'],
    anthropic: [
      settings.anthropic_api_key_configured,
      'An Anthropic API key is already stored.',
      'No Anthropic API key has been stored yet.',
    ],
    github_models: [
      settings.github_models_api_key_configured,
      'A GitHub token for GitHub Models is already stored.',
      'No GitHub token for GitHub Models has been stored yet.',
    ],
    azure_openai: [
      settings.azure_openai_api_key_configured,
      'An Azure OpenAI API key is already stored.',
      'No Azure OpenAI API key has been stored yet.',
    ],
  };
  if (provider === 'azure_openai' && azureAuthMode === 'entra') {
    return 'Entra ID auth stores no secret: Janus uses the Azure identity of its host (az login, or a managed identity).';
  }
  const [stored, storedMessage, missingMessage] = status[provider];
  return stored ? storedMessage : missingMessage;
};
