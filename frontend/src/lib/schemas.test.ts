import { describe, expect, it } from 'vitest';
import { plannerSettingsSchema } from './schemas';

const minimalSettings = {
  initial_sync_email_limit: 100,
  llm_provider: 'ollama',
  llm_model: 'llama3:latest',
  ollama_model: 'llama3:latest',
  ollama_base_url: 'http://localhost:11434',
  ollama_api_key_configured: false,
  openai_model: 'gpt-4o-mini',
  openai_api_key_configured: false,
  openrouter_model: 'openrouter/auto',
  openrouter_api_key_configured: false,
  gemini_model: 'gemini-2.5-flash',
  gemini_api_key_configured: false,
  anthropic_model: 'claude-opus-5',
  anthropic_api_key_configured: false,
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

describe('plannerSettingsSchema', () => {
  it('keeps the saved per-surface model choices instead of stripping them', () => {
    const parsed = plannerSettingsSchema.parse({
      ...minimalSettings,
      chat_llm_provider: 'anthropic',
      chat_llm_model: 'claude-sonnet-5',
      utility_llm_provider: null,
      utility_llm_model: 'tiny',
    });
    expect(parsed).toMatchObject({
      chat_llm_provider: 'anthropic',
      chat_llm_model: 'claude-sonnet-5',
      utility_llm_provider: null,
      utility_llm_model: 'tiny',
    });
  });
});
