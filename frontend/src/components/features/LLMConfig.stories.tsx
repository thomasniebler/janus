import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { plannerApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { LLMConfig } from './LLMConfig';
import type { LLMModelListResponse, PlannerSettings } from '../../types';

const baseSettings: PlannerSettings = {
  initial_sync_email_limit: 100,
  llm_provider: 'ollama',
  llm_model: 'llama3.1:8b',
  ollama_model: 'llama3.1:8b',
  ollama_base_url: 'http://localhost:11434',
  ollama_api_key_configured: false,
  openai_model: 'gpt-4o-mini',
  openai_base_url: null,
  openai_api_key_configured: false,
  openrouter_model: 'openrouter/auto',
  openrouter_base_url: 'https://openrouter.ai/api/v1',
  openrouter_api_key_configured: false,
  gemini_model: 'gemini-2.5-flash',
  gemini_api_key_configured: false,
  anthropic_model: 'claude-sonnet-5',
  anthropic_base_url: null,
  anthropic_api_key_configured: true,
  github_models_model: 'openai/gpt-4.1-mini',
  github_models_base_url: null,
  github_models_api_key_configured: false,
  azure_openai_endpoint: null,
  azure_openai_deployment: null,
  azure_openai_api_version: '2024-10-21',
  azure_openai_auth_mode: 'entra',
  azure_openai_api_key_configured: false,
  github_token_configured: false,
  topic_tracking_available: true,
  topic_tracking_default_enabled: true,
  topic_tracking_default_scope: 'important_and_digest',
  topic_tracking_default_min_confidence: 'medium',
  topic_tracking_default_min_confirmation_mentions: 2,
  topic_tracking_max_active_topics: 20,
  topic_tracking_max_context_topics: 5,
  topic_tracking_review_required: true,
  embedding_min_similarity: 0.5,
  embedding_default_models: { ollama: 'nomic-embed-text', openai: 'text-embedding-3-small', gemini: 'gemini-embedding-001' },
  vector_search_available: true,
};

const modelsFor = (provider: string): LLMModelListResponse => {
  switch (provider) {
    case 'ollama':
      return { models: [{ id: 'llama3.1:8b', label: 'Llama 3.1 8B' }, { id: 'mistral-nemo', label: 'Mistral Nemo' }] };
    case 'anthropic':
      return { models: [{ id: 'claude-sonnet-5', label: 'Claude Sonnet 5' }, { id: 'claude-opus-5', label: 'Claude Opus 5' }] };
    default:
      return { models: [], error: 'unavailable in this preview' };
  }
};

const seed =
  (settings: PlannerSettings) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['system-settings'], settings);
  };

const meta = {
  title: 'Features/LLMConfig',
  component: LLMConfig,
  args: { isAdmin: true },
  decorators: [
    withApiOverride(
      plannerApi,
      'listLLMModels',
      async ({ provider }) => ({ data: modelsFor(provider) }) as never
    ),
  ],
} satisfies Meta<typeof LLMConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

export const OllamaSelected: Story = {
  decorators: [withQueryData(seed(baseSettings))],
};

export const AnthropicSelected: Story = {
  decorators: [withQueryData(seed({ ...baseSettings, llm_provider: 'anthropic' }))],
};

export const OpenAISelected: Story = {
  decorators: [withQueryData(seed({ ...baseSettings, llm_provider: 'openai' }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(plannerApi, 'getSettings', () => neverResolving())],
};

/** A non-admin sees the configured backend but cannot test or save it (JAN-222). */
export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed({ ...baseSettings, llm_provider: 'openai', openai_api_key_configured: true }))],
};

/** GitHub Models with a stored token (JAN-340). */
export const GitHubModelsSelected: Story = {
  decorators: [withQueryData(seed({ ...baseSettings, llm_provider: 'github_models', github_models_api_key_configured: true }))],
};

/** Azure OpenAI with Entra ID auth: no key is stored or asked for (JAN-340). */
export const AzureOpenAIEntraSelected: Story = {
  decorators: [
    withQueryData(
      seed({
        ...baseSettings,
        llm_provider: 'azure_openai',
        azure_openai_endpoint: 'https://my-foundry.cognitiveservices.azure.com',
        azure_openai_deployment: 'Kimi-K2.6',
      })
    ),
  ],
};

/** Azure OpenAI with API key auth and a stored key (JAN-340). */
export const AzureOpenAIApiKeySelected: Story = {
  decorators: [
    withQueryData(
      seed({
        ...baseSettings,
        llm_provider: 'azure_openai',
        azure_openai_endpoint: 'https://my-resource.openai.azure.com',
        azure_openai_deployment: 'gpt-4.1-mini',
        azure_openai_auth_mode: 'api_key',
        azure_openai_api_key_configured: true,
      })
    ),
  ],
};
