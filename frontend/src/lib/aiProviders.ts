/** Every provider on AI & Models → Models, in the order that tab lists them. */
export const AI_PROVIDER_LABELS = {
  ollama: 'Ollama / Llama',
  openai: 'OpenAI',
  openrouter: 'OpenRouter',
  gemini: 'Google Gemini',
  anthropic: 'Anthropic',
  github_models: 'GitHub Models',
  azure_openai: 'Azure OpenAI',
} as const;

export type AiProviderId = keyof typeof AI_PROVIDER_LABELS;

/**
 * The providers a capability (embeddings, images) cannot use, as `[id, label]`. Capability pickers list them
 * disabled, so every picker shows the same providers as the Models tab and says why one is missing.
 */
export const unsupportedProviders = (supported: readonly string[]): Array<[AiProviderId, string]> =>
  (Object.keys(AI_PROVIDER_LABELS) as AiProviderId[])
    .filter((id) => !supported.includes(id))
    .map((id) => [id, AI_PROVIDER_LABELS[id]]);
