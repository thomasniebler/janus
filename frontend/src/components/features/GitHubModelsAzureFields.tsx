import React from 'react';
import { Input, Select } from '../ui/Input';
import { LLMModelSelect } from './LLMModelSelect';
import type { GitHubModelsAzureDraft } from '../../lib/llmProviderDrafts';
import type { AzureOpenAIAuthMode } from '../../lib/llmProviders';
import type { PlannerSettings } from '../../types';

interface FieldsProps {
  draft: GitHubModelsAzureDraft;
  settings: PlannerSettings;
  updateDraft: <K extends keyof GitHubModelsAzureDraft>(key: K, value: GitHubModelsAzureDraft[K]) => void;
}

/** GitHub Models (JAN-340): model picker from the GitHub Models catalog, a GitHub token, optional base URL. */
export const GitHubModelsFields: React.FC<FieldsProps> = ({ draft, settings, updateDraft }) => (
  <div className="grid gap-4 md:grid-cols-2">
    <LLMModelSelect
      provider="github_models"
      label="GitHub Models Model"
      value={draft.github_models_model}
      onChange={(value) => updateDraft('github_models_model', value)}
      placeholder="openai/gpt-4.1-mini"
      apiKey={draft.github_models_api_key}
      baseUrl={draft.github_models_base_url}
    />
    <Input
      label="GitHub Token"
      type="password"
      value={draft.github_models_api_key}
      onChange={(event) => updateDraft('github_models_api_key', event.target.value)}
      placeholder={
        settings.github_models_api_key_configured
          ? 'Stored token is configured. Enter to replace.'
          : 'github_pat_... (models:read) or `gh auth token`'
      }
    />
    <Input
      label="GitHub Models Base URL"
      value={draft.github_models_base_url}
      onChange={(event) => updateDraft('github_models_base_url', event.target.value)}
      placeholder="Optional, defaults to https://models.github.ai/inference"
      className="md:col-span-2"
    />
  </div>
);

/**
 * Azure OpenAI / Azure AI Foundry (JAN-340): endpoint, deployment, API version and auth mode.
 * The deployment picker lists the resource's deployments with the draft endpoint and credential
 * (JAN-349), falling back to free text when they cannot be listed; the API key field only
 * appears in API key mode, since Entra ID auth uses the host's identity.
 */
export const AzureOpenAIFields: React.FC<FieldsProps> = ({ draft, settings, updateDraft }) => (
  <div className="grid gap-4 md:grid-cols-2">
    <Input
      label="Azure OpenAI Endpoint"
      value={draft.azure_openai_endpoint}
      onChange={(event) => updateDraft('azure_openai_endpoint', event.target.value)}
      placeholder="https://<resource>.openai.azure.com"
      className="md:col-span-2"
    />
    <LLMModelSelect
      provider="azure_openai"
      label="Deployment Name"
      value={draft.azure_openai_deployment}
      onChange={(value) => updateDraft('azure_openai_deployment', value)}
      placeholder="gpt-4.1-mini"
      apiKey={draft.azure_openai_auth_mode === 'api_key' ? draft.azure_openai_api_key : ''}
      baseUrl={draft.azure_openai_endpoint}
      authMode={draft.azure_openai_auth_mode}
    />
    <Input
      label="API Version"
      value={draft.azure_openai_api_version}
      onChange={(event) => updateDraft('azure_openai_api_version', event.target.value)}
      placeholder="preview"
    />
    <Select
      label="Auth Mode"
      value={draft.azure_openai_auth_mode}
      onChange={(event) => updateDraft('azure_openai_auth_mode', event.target.value as AzureOpenAIAuthMode)}
    >
      <option value="entra">Entra ID (az login / managed identity)</option>
      <option value="api_key">API key</option>
    </Select>
    {draft.azure_openai_auth_mode === 'api_key' && (
      <Input
        label="Azure OpenAI API Key"
        type="password"
        value={draft.azure_openai_api_key}
        onChange={(event) => updateDraft('azure_openai_api_key', event.target.value)}
        placeholder={settings.azure_openai_api_key_configured ? 'Stored key is configured. Enter to replace.' : 'Key 1 or Key 2'}
      />
    )}
  </div>
);
