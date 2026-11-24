/** The LLM backends Janus can use; mirrors the backend `LLMProvider` enum. */
export type LLMProviderId = 'ollama' | 'openai' | 'openrouter' | 'gemini' | 'anthropic' | 'github_models' | 'azure_openai';

/** How Janus authenticates to Azure OpenAI (JAN-340): the host's Entra ID identity or a stored key. */
export type AzureOpenAIAuthMode = 'entra' | 'api_key';

/** Draft credentials for listing a provider's models without saving them (JAN-215). */
export interface LLMModelListRequest {
  provider: LLMProviderId;
  api_key?: string;
  base_url?: string;
  /** Azure OpenAI only (JAN-349): the draft auth mode; omitted means the stored one. */
  auth_mode?: AzureOpenAIAuthMode;
}

/** The GitHub Models and Azure OpenAI fields of the system settings (JAN-340); secrets only as flags. */
export interface GitHubModelsAzureSettings {
  github_models_model: string;
  github_models_base_url?: string | null;
  github_models_api_key_configured: boolean;
  azure_openai_endpoint?: string | null;
  /** The deployment name doubles as the model name for Azure OpenAI. */
  azure_openai_deployment?: string | null;
  azure_openai_api_version: string;
  azure_openai_auth_mode: AzureOpenAIAuthMode;
  azure_openai_api_key_configured: boolean;
}

/** The writable GitHub Models and Azure OpenAI settings; an empty URL, endpoint or key clears it. */
export interface GitHubModelsAzureSettingsUpdate {
  github_models_model?: string;
  github_models_api_key?: string;
  github_models_base_url?: string;
  azure_openai_endpoint?: string;
  azure_openai_deployment?: string;
  azure_openai_api_version?: string;
  azure_openai_api_key?: string;
  azure_openai_auth_mode?: AzureOpenAIAuthMode;
}
