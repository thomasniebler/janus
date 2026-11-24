import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Building2, Cloud, GitBranch, Globe, HardDrive, KeyRound, MessageSquare, RefreshCw, Sparkles } from 'lucide-react';
import { plannerApi } from '../../api';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { FeedbackBanner, type Feedback } from '../ui/FeedbackBanner';
import { requestErrorMessage } from '../../lib/requestErrors';
import { LLMModelSelect } from './LLMModelSelect';
import { AzureOpenAIFields, GitHubModelsFields } from './GitHubModelsAzureFields';
import {
  buildGitHubModelsAzureDraft,
  secretStatusMessage,
  toGitHubModelsAzurePayload,
  type GitHubModelsAzureDraft,
} from '../../lib/llmProviderDrafts';
import type { PlannerSettings, PlannerSettingsUpdate } from '../../types';

type ProviderId = PlannerSettings['llm_provider'];

type DraftSettings = PlannerSettingsUpdate & {
  llm_provider: ProviderId;
  ollama_model: string;
  ollama_base_url: string;
  ollama_api_key: string;
  openai_model: string;
  openai_base_url: string;
  openai_api_key: string;
  openrouter_model: string;
  openrouter_base_url: string;
  openrouter_api_key: string;
  gemini_model: string;
  gemini_api_key: string;
  anthropic_model: string;
  anthropic_base_url: string;
  anthropic_api_key: string;
} & GitHubModelsAzureDraft;

type ProviderCard = {
  id: ProviderId;
  label: string;
  icon: React.ReactNode;
  description: string;
  required: string[];
  optional: string[];
};

const PROVIDERS: ProviderCard[] = [
  {
    id: 'ollama',
    label: 'Ollama / Llama',
    icon: <HardDrive size={18} />,
    description: 'Use either a local Ollama runtime or the hosted Ollama API at https://ollama.com/api.',
    required: ['Model name', 'Base URL'],
    optional: ['API key'],
  },
  {
    id: 'openai',
    label: 'OpenAI',
    icon: <Cloud size={18} />,
    description: 'Use OpenAI-hosted chat models or an OpenAI-compatible endpoint.',
    required: ['Model name', 'API key'],
    optional: ['Base URL'],
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    icon: <Globe size={18} />,
    description: "Use OpenRouter's OpenAI-compatible routing API.",
    required: ['Model name', 'API key'],
    optional: ['Base URL'],
  },
  {
    id: 'gemini',
    label: 'Gemini',
    icon: <Sparkles size={18} />,
    description: 'Use Google Gemini through a Google AI Studio API key.',
    required: ['Model name', 'API key'],
    optional: [],
  },
  {
    id: 'anthropic',
    label: 'Anthropic',
    icon: <MessageSquare size={18} />,
    description: 'Use Anthropic Claude models through an Anthropic API key.',
    required: ['Model name', 'API key'],
    optional: ['Base URL'],
  },
  {
    id: 'github_models',
    label: 'GitHub Models',
    icon: <GitBranch size={18} />,
    description: 'Use the models in GitHub Models with a GitHub token (models:read scope, or `gh auth token`).',
    required: ['Model name', 'GitHub token'],
    optional: ['Base URL'],
  },
  {
    id: 'azure_openai',
    label: 'Azure OpenAI',
    icon: <Building2 size={18} />,
    description: 'Use a model deployment in Azure OpenAI or Azure AI Foundry, signed in with Entra ID or an API key.',
    required: ['Endpoint', 'Deployment name', 'Auth mode'],
    optional: ['API version', 'API key (API key auth only)'],
  },
];

const buildDraftFromSettings = (settings: PlannerSettings): DraftSettings => ({
  llm_provider: settings.llm_provider,
  ollama_model: settings.ollama_model,
  ollama_base_url: settings.ollama_base_url,
  ollama_api_key: '',
  openai_model: settings.openai_model,
  openai_base_url: settings.openai_base_url || '',
  openai_api_key: '',
  openrouter_model: settings.openrouter_model,
  openrouter_base_url: settings.openrouter_base_url || '',
  openrouter_api_key: '',
  gemini_model: settings.gemini_model,
  gemini_api_key: '',
  anthropic_model: settings.anthropic_model,
  anthropic_base_url: settings.anthropic_base_url || '',
  anthropic_api_key: '',
  ...buildGitHubModelsAzureDraft(settings),
});

const toPayload = (draft: DraftSettings): PlannerSettingsUpdate => ({
  llm_provider: draft.llm_provider,
  ollama_model: draft.ollama_model.trim(),
  ollama_base_url: draft.ollama_base_url.trim(),
  ollama_api_key: draft.ollama_api_key.trim() || undefined,
  openai_model: draft.openai_model.trim(),
  openai_base_url: draft.openai_base_url.trim(),
  openai_api_key: draft.openai_api_key.trim() || undefined,
  openrouter_model: draft.openrouter_model.trim(),
  openrouter_base_url: draft.openrouter_base_url.trim(),
  openrouter_api_key: draft.openrouter_api_key.trim() || undefined,
  gemini_model: draft.gemini_model.trim(),
  gemini_api_key: draft.gemini_api_key.trim() || undefined,
  anthropic_model: draft.anthropic_model.trim(),
  anthropic_base_url: draft.anthropic_base_url.trim(),
  anthropic_api_key: draft.anthropic_api_key.trim() || undefined,
  ...toGitHubModelsAzurePayload(draft),
});

/** The default AI backend; instance-wide, so only an admin can test or save it (JAN-222). */
export const LLMConfig: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const queryClient = useQueryClient();
  const [draftOverrides, setDraftOverrides] = useState<Partial<DraftSettings>>({});
  const [testResult, setTestResult] = useState<Feedback | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['system-settings'],
    queryFn: async () => (await plannerApi.getSettings()).data,
  });

  const draft = useMemo(() => {
    if (!settings) {
      return null;
    }

    return {
      ...buildDraftFromSettings(settings),
      ...draftOverrides,
    } satisfies DraftSettings;
  }, [draftOverrides, settings]);

  const saveMutation = useMutation({
    mutationFn: (payload: PlannerSettingsUpdate) => plannerApi.updateSettings(payload),
    onSuccess: (response) => {
      queryClient.setQueryData(['system-settings'], response.data);
      setDraftOverrides({});
      setTestResult({ status: 'success', message: 'AI backend settings saved.' });
    },
    onError: (error) => setTestResult({ status: 'error', message: requestErrorMessage(error, 'Failed to save settings.') }),
  });

  const testMutation = useMutation({
    mutationFn: (payload: PlannerSettingsUpdate) => plannerApi.testLLM(payload),
    onSuccess: (response) =>
      setTestResult({ status: response.data.status === 'success' ? 'success' : 'error', message: response.data.message }),
    onError: (error) => setTestResult({ status: 'error', message: requestErrorMessage(error, 'LLM test failed.') }),
  });

  const updateDraft = <K extends keyof DraftSettings>(key: K, value: DraftSettings[K]) => {
    setDraftOverrides((current) => ({ ...current, [key]: value }));
    setTestResult(null);
  };
  // DraftSettings includes every GitHubModelsAzureDraft key with the same value type.
  const updateProviderDraft = updateDraft as <K extends keyof GitHubModelsAzureDraft>(key: K, value: GitHubModelsAzureDraft[K]) => void;

  if (isLoading || !settings || !draft) {
    return (
      <Card>
        <CardHeader>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Bot size={24} className="text-primary" />
            AI Configuration
          </h2>
        </CardHeader>
        <CardContent className="h-40 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  const selectedProvider = PROVIDERS.find((provider) => provider.id === draft.llm_provider) || PROVIDERS[0];
  const payload = toPayload(draft);

  return (
    <Card>
      <CardHeader>
        <div className="space-y-2">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Bot size={24} className="text-primary" />
            AI Configuration
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Pick the backend Janus should use, then fill in the provider-specific parameters it requires.
          </p>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {PROVIDERS.map((provider) => (
            <button
              key={provider.id}
              type="button"
              onClick={() => updateDraft('llm_provider', provider.id)}
              className={`rounded-2xl border p-4 text-left transition-all ${
                draft.llm_provider === provider.id
                  ? 'border-primary bg-primary/5 shadow-sm'
                  : 'border-gray-200 dark:border-gray-800 hover:border-primary/40'
              }`}
            >
              <div className="flex items-center gap-2 font-bold">
                <span className="text-primary">{provider.icon}</span>
                {provider.label}
              </div>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">{provider.description}</p>
            </button>
          ))}
        </div>

        <div className="rounded-2xl border border-gray-200 dark:border-gray-800 p-5 space-y-5">
          <div className="space-y-1">
            <h3 className="text-lg font-bold">{selectedProvider.label} settings</h3>
            <p className="text-sm text-gray-500 dark:text-gray-400">{selectedProvider.description}</p>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-xl bg-gray-50 dark:bg-gray-800/50 p-4">
              <p className="text-xs font-bold uppercase tracking-wide text-gray-400">Required parameters</p>
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">{selectedProvider.required.join(', ')}</p>
            </div>
            <div className="rounded-xl bg-gray-50 dark:bg-gray-800/50 p-4">
              <p className="text-xs font-bold uppercase tracking-wide text-gray-400">Optional parameters</p>
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                {selectedProvider.optional.length > 0 ? selectedProvider.optional.join(', ') : 'None'}
              </p>
            </div>
          </div>

          {draft.llm_provider === 'ollama' && (
            <div className="grid gap-4 md:grid-cols-2">
              <LLMModelSelect
                provider="ollama"
                label="Ollama Model"
                value={draft.ollama_model}
                onChange={(value) => updateDraft('ollama_model', value)}
                placeholder="llama3:latest"
                apiKey={draft.ollama_api_key}
                baseUrl={draft.ollama_base_url}
              />
              <Input
                label="Ollama Base URL"
                value={draft.ollama_base_url}
                onChange={(event) => updateDraft('ollama_base_url', event.target.value)}
                placeholder="http://localhost:11434"
              />
              <Input
                label="Ollama API Key"
                type="password"
                value={draft.ollama_api_key}
                onChange={(event) => updateDraft('ollama_api_key', event.target.value)}
                placeholder={
                  settings.ollama_api_key_configured
                    ? 'Stored key is configured. Enter to replace.'
                    : 'Required for https://ollama.com/api'
                }
                className="md:col-span-2"
              />
            </div>
          )}

          {draft.llm_provider === 'openai' && (
            <div className="grid gap-4 md:grid-cols-2">
              <LLMModelSelect
                provider="openai"
                label="OpenAI Model"
                value={draft.openai_model}
                onChange={(value) => updateDraft('openai_model', value)}
                placeholder="gpt-4o-mini"
                apiKey={draft.openai_api_key}
                baseUrl={draft.openai_base_url}
              />
              <Input
                label="OpenAI API Key"
                type="password"
                value={draft.openai_api_key}
                onChange={(event) => updateDraft('openai_api_key', event.target.value)}
                placeholder={settings.openai_api_key_configured ? 'Stored key is configured. Enter to replace.' : 'sk-...'}
              />
              <Input
                label="OpenAI Base URL"
                value={draft.openai_base_url}
                onChange={(event) => updateDraft('openai_base_url', event.target.value)}
                placeholder="Optional for compatible endpoints"
                className="md:col-span-2"
              />
            </div>
          )}

          {draft.llm_provider === 'openrouter' && (
            <div className="grid gap-4 md:grid-cols-2">
              <LLMModelSelect
                provider="openrouter"
                label="OpenRouter Model"
                value={draft.openrouter_model}
                onChange={(value) => updateDraft('openrouter_model', value)}
                placeholder="openrouter/auto"
                apiKey={draft.openrouter_api_key}
                baseUrl={draft.openrouter_base_url}
              />
              <Input
                label="OpenRouter API Key"
                type="password"
                value={draft.openrouter_api_key}
                onChange={(event) => updateDraft('openrouter_api_key', event.target.value)}
                placeholder={
                  settings.openrouter_api_key_configured ? 'Stored key is configured. Enter to replace.' : 'sk-or-v1-...'
                }
              />
              <Input
                label="OpenRouter Base URL"
                value={draft.openrouter_base_url}
                onChange={(event) => updateDraft('openrouter_base_url', event.target.value)}
                placeholder="https://openrouter.ai/api/v1"
                className="md:col-span-2"
              />
            </div>
          )}

          {draft.llm_provider === 'gemini' && (
            <div className="grid gap-4 md:grid-cols-2">
              <LLMModelSelect
                provider="gemini"
                label="Gemini Model"
                value={draft.gemini_model}
                onChange={(value) => updateDraft('gemini_model', value)}
                placeholder="gemini-2.5-flash"
                apiKey={draft.gemini_api_key}
              />
              <Input
                label="Gemini API Key"
                type="password"
                value={draft.gemini_api_key}
                onChange={(event) => updateDraft('gemini_api_key', event.target.value)}
                placeholder={settings.gemini_api_key_configured ? 'Stored key is configured. Enter to replace.' : 'AIza...'}
              />
            </div>
          )}

          {draft.llm_provider === 'anthropic' && (
            <div className="grid gap-4 md:grid-cols-2">
              <LLMModelSelect
                provider="anthropic"
                label="Anthropic Model"
                value={draft.anthropic_model}
                onChange={(value) => updateDraft('anthropic_model', value)}
                placeholder="claude-opus-5"
                apiKey={draft.anthropic_api_key}
                baseUrl={draft.anthropic_base_url}
              />
              <Input
                label="Anthropic API Key"
                type="password"
                value={draft.anthropic_api_key}
                onChange={(event) => updateDraft('anthropic_api_key', event.target.value)}
                placeholder={
                  settings.anthropic_api_key_configured ? 'Stored key is configured. Enter to replace.' : 'sk-ant-...'
                }
              />
              <Input
                label="Anthropic Base URL"
                value={draft.anthropic_base_url}
                onChange={(event) => updateDraft('anthropic_base_url', event.target.value)}
                placeholder="Optional, defaults to https://api.anthropic.com"
                className="md:col-span-2"
              />
            </div>
          )}

          {draft.llm_provider === 'github_models' && (
            <GitHubModelsFields draft={draft} settings={settings} updateDraft={updateProviderDraft} />
          )}
          {draft.llm_provider === 'azure_openai' && <AzureOpenAIFields draft={draft} settings={settings} updateDraft={updateProviderDraft} />}

          <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-4 text-sm text-gray-600 dark:text-gray-300">
            <div className="flex items-center gap-2 font-bold mb-2">
              <KeyRound size={16} className="text-primary" />
              Secret status
            </div>
            <p>{secretStatusMessage(draft.llm_provider, settings, draft.azure_openai_auth_mode)}</p>
          </div>
        </div>

        {isAdmin ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              onClick={() => testMutation.mutate(payload)}
              isLoading={testMutation.isPending}
              icon={<RefreshCw size={16} />}
            >
              Test Selected Backend
            </Button>
            <Button onClick={() => saveMutation.mutate(payload)} isLoading={saveMutation.isPending}>
              Save Backend Settings
            </Button>
          </div>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">Only an administrator can test or change the AI backend.</p>
        )}

        <FeedbackBanner feedback={testResult} />
      </CardContent>
    </Card>
  );
};
