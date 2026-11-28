import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, Cloud, Globe, KeyRound, RefreshCw } from 'lucide-react';
import { webSearchApi } from '../../api';
import type { NativeSearchProvider, WebSearchProvider, WebSearchSettings, WebSearchSettingsUpdate } from '../../types';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Button } from '../ui/Button';
import { Input, Select } from '../ui/Input';
import { Badge } from '../ui/Badge';
import { FeedbackBanner, type Feedback } from '../ui/FeedbackBanner';
import { requestErrorMessage } from '../../lib/requestErrors';

/** The chat providers that can search with their own tool, labelled as on the Models tab. */
const NATIVE_LABELS: Record<NativeSearchProvider, string> = {
  openai: 'OpenAI',
  gemini: 'Google Gemini',
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
};
const NATIVE_PROVIDERS = Object.keys(NATIVE_LABELS) as NativeSearchProvider[];
const isNative = (provider: string): provider is NativeSearchProvider => provider in NATIVE_LABELS;

const buildPayload = (draft: WebSearchSettingsUpdate): WebSearchSettingsUpdate => {
  const payload: WebSearchSettingsUpdate = {};
  if ('provider' in draft) payload.provider = draft.provider || '';
  if ('api_key' in draft) payload.api_key = draft.api_key?.trim() ?? '';
  if ('base_url' in draft) payload.base_url = draft.base_url?.trim() ?? '';
  if ('model' in draft) payload.model = draft.model?.trim() ?? '';
  return payload;
};

const ProviderHelp: React.FC = () => (
    <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 space-y-2 text-sm text-gray-500 dark:text-gray-400">
      <p>
        <strong className="text-gray-700 dark:text-gray-200">SearXNG</strong> — point Janus at a self-hosted SearXNG
        instance with the JSON output format enabled. No API key needed.
      </p>
      <p>
        <strong className="text-gray-700 dark:text-gray-200">Brave Search</strong> — uses the Brave Search API and
        requires an API key.
      </p>
      <p>
        <strong className="text-gray-700 dark:text-gray-200">OpenAI, Gemini, Anthropic, OpenRouter</strong> — the
        provider&apos;s own search tool answers, using the API key you stored on the Models tab. The search query goes
        to that cloud provider and each search is billed to that account.
      </p>
      <p>
        Chat can search once a provider is set up. Unattended runs (triggers that select it, follow-ups, background
        tasks) can search too unless you switch <em>Web search</em> off under <strong>Built-in Janus tools</strong>.
      </p>
    </div>
);

interface NativeSearchFieldsProps {
  provider: NativeSearchProvider;
  model: string;
  defaultModel: string;
  keyMissing: boolean;
  isAdmin: boolean;
  onModelChange: (model: string) => void;
}

/** The model field and the cloud and key hints for a provider-native search backend. */
const NativeSearchFields: React.FC<NativeSearchFieldsProps> = ({ provider, model, defaultModel, keyMissing, isAdmin, onModelChange }) => {
  const label = NATIVE_LABELS[provider];
  return (
    <>
      <Input
        label="Search model"
        value={model}
        disabled={!isAdmin}
        onChange={(e) => onModelChange(e.target.value)}
        placeholder={defaultModel}
      />
      <p className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400" data-testid="web-search-cloud-hint">
        <Cloud size={14} className="mt-0.5 shrink-0" />
        Cloud search: the query Janus writes for each search is sent to {label}. Pick a model that supports web search.
      </p>
      {keyMissing && (
        <p className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400" data-testid="web-search-key-missing">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          No {label} API key is stored yet, so Janus cannot search until you add one under AI &amp; Models → Models.
        </p>
      )}
    </>
  );
};

interface BraveKeyFieldsProps {
  value: string;
  configured: boolean;
  isAdmin: boolean;
  saving: boolean;
  onChange: (apiKey: string) => void;
}

/** The write-only Brave API key field; an empty string in the draft clears the stored key. */
const BraveKeyFields: React.FC<BraveKeyFieldsProps> = ({ value, configured, isAdmin, saving, onChange }) => (
  <>
    <Input
      label="Brave Search API key"
      type="password"
      value={value}
      disabled={!isAdmin}
      onChange={(e) => onChange(e.target.value)}
      placeholder={configured ? 'API key configured — paste to replace' : 'BSA…'}
    />
    {configured && isAdmin && (
      <div className="flex justify-end">
        <Button type="button" variant="ghost" onClick={() => onChange('')} disabled={saving}>
          Clear stored key
        </Button>
      </div>
    )}
  </>
);

interface ProviderFieldsProps {
  provider: WebSearchProvider | '';
  draft: WebSearchSettingsUpdate;
  settings: WebSearchSettings | undefined;
  isAdmin: boolean;
  saving: boolean;
  updateDraft: (changes: WebSearchSettingsUpdate) => void;
}

/** The provider picker and the fields of the chosen backend. */
const ProviderFields: React.FC<ProviderFieldsProps> = ({ provider, draft, settings, isAdmin, saving, updateDraft }) => {
  const apiKeyConfigured = settings?.api_key_configured ?? false;
  const nativeProvider = isNative(provider) ? provider : null;
  const keyMissing = nativeProvider !== null && !(settings?.provider_keys?.[nativeProvider] ?? false);
  const modelValue = 'model' in draft ? (draft.model ?? '') : provider === settings?.provider ? (settings?.model ?? '') : '';
  const baseUrlValue = 'base_url' in draft ? (draft.base_url ?? '') : (settings?.base_url ?? '');
  return (
    <div className="grid gap-4">
      <Select
        label="Provider"
        value={provider}
        disabled={!isAdmin}
        onChange={(e) => {
          const next = e.target.value as WebSearchProvider | '';
          // A model belongs to its provider: start from the new provider's default.
          updateDraft(next && isNative(next) ? { provider: next, model: '' } : { provider: next });
        }}
      >
        <option value="">Disabled</option>
        <optgroup label="Independent search">
          <option value="searxng">SearXNG (self-hosted, stays local)</option>
          <option value="brave">Brave Search API (third party)</option>
        </optgroup>
        <optgroup label="Through your AI provider (third party)">
          {NATIVE_PROVIDERS.map((id) => (
            <option key={id} value={id}>
              {NATIVE_LABELS[id]}
            </option>
          ))}
        </optgroup>
      </Select>

      {nativeProvider && (
        <NativeSearchFields
          provider={nativeProvider}
          model={modelValue}
          defaultModel={settings?.default_models?.[nativeProvider] ?? ''}
          keyMissing={keyMissing}
          isAdmin={isAdmin}
          onModelChange={(model) => updateDraft({ model })}
        />
      )}

      {provider === 'searxng' && (
        <Input
          label="SearXNG base URL"
          value={baseUrlValue}
          disabled={!isAdmin}
          onChange={(e) => updateDraft({ base_url: e.target.value })}
          placeholder="http://localhost:8888"
        />
      )}

      {provider === 'brave' && (
        <BraveKeyFields
          value={draft.api_key ?? ''}
          configured={apiKeyConfigured}
          isAdmin={isAdmin}
          saving={saving}
          onChange={(api_key) => updateDraft({ api_key })}
        />
      )}
    </div>
  );
};

export const WebSearchConfig: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<WebSearchSettingsUpdate>({});
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['web-search-settings'],
    queryFn: async () => (await webSearchApi.getSettings()).data,
  });

  const updateDraft = (changes: WebSearchSettingsUpdate) => {
    setDraft((current) => ({ ...current, ...changes }));
    setFeedback(null);
  };

  const saveMutation = useMutation({
    mutationFn: () => webSearchApi.updateSettings(buildPayload(draft)),
    onSuccess: (response) => {
      queryClient.setQueryData(['web-search-settings'], response.data);
      setDraft({});
      setFeedback({ status: 'success', message: 'Web search settings saved.' });
    },
    onError: (error) => setFeedback({ status: 'error', message: requestErrorMessage(error, 'Failed to save web search settings.') }),
  });

  const header = (
    <h2 className="text-xl font-bold flex items-center gap-2">
      <Globe size={24} className="text-primary" />
      Web Search
    </h2>
  );

  if (isLoading) {
    return (
      <Card>
        <CardHeader>{header}</CardHeader>
        <CardContent className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  const provider = ('provider' in draft ? draft.provider : settings?.provider) || '';
  const canSave = Object.keys(draft).length > 0;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            {header}
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Let Janus search the web from chat. Only the search query the assistant writes is sent to the provider —
              never your conversation or email content.
            </p>
          </div>
          <Badge variant={settings?.configured ? 'success' : 'gray'} className="shrink-0">
            {settings?.configured ? 'Enabled' : 'Not configured'}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        <ProviderHelp />

        <ProviderFields
          provider={provider}
          draft={draft}
          settings={settings}
          isAdmin={isAdmin}
          saving={saveMutation.isPending}
          updateDraft={updateDraft}
        />

        <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-3 text-sm text-gray-600 dark:text-gray-300 flex items-center gap-2">
          <KeyRound size={15} className="text-primary shrink-0" />
          {isAdmin
            ? 'API keys are stored encrypted at rest. OpenAI, Gemini, Anthropic and OpenRouter search reuse the key from the Models tab.'
            : 'Only an administrator can change web search settings.'}
        </div>

        {isAdmin && (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              onClick={() => saveMutation.mutate()}
              isLoading={saveMutation.isPending}
              disabled={!canSave}
              icon={<Check size={16} />}
            >
              Save Settings
            </Button>
          </div>
        )}

        <FeedbackBanner feedback={feedback} />
      </CardContent>
    </Card>
  );
};
