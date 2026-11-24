import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Globe, KeyRound, RefreshCw } from 'lucide-react';
import { webSearchApi } from '../../api';
import type { WebSearchProvider, WebSearchSettingsUpdate } from '../../types';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Button } from '../ui/Button';
import { Input, Select } from '../ui/Input';
import { Badge } from '../ui/Badge';
import { FeedbackBanner, type Feedback } from '../ui/FeedbackBanner';
import { requestErrorMessage } from '../../lib/requestErrors';

const buildPayload = (draft: WebSearchSettingsUpdate): WebSearchSettingsUpdate => {
  const payload: WebSearchSettingsUpdate = {};
  if ('provider' in draft) payload.provider = draft.provider || '';
  if ('api_key' in draft) payload.api_key = draft.api_key?.trim() ?? '';
  if ('base_url' in draft) payload.base_url = draft.base_url?.trim() ?? '';
  return payload;
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
  const apiKeyConfigured = settings?.api_key_configured ?? false;
  const baseUrlValue = 'base_url' in draft ? (draft.base_url ?? '') : (settings?.base_url ?? '');
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
        <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 space-y-2 text-sm text-gray-500 dark:text-gray-400">
          <p>
            <strong className="text-gray-700 dark:text-gray-200">SearXNG</strong> — point Janus at a self-hosted SearXNG
            instance with the JSON output format enabled. No API key needed.
          </p>
          <p>
            <strong className="text-gray-700 dark:text-gray-200">Brave Search</strong> — uses the Brave Search API and
            requires an API key.
          </p>
          <p>Web search is available in chat only; scheduled triggers and delegated tasks cannot use it.</p>
        </div>

        <div className="grid gap-4">
          <Select
            label="Provider"
            value={provider}
            disabled={!isAdmin}
            onChange={(e) => updateDraft({ provider: e.target.value as WebSearchProvider | '' })}
          >
            <option value="">Disabled</option>
            <option value="searxng">SearXNG (self-hosted)</option>
            <option value="brave">Brave Search API</option>
          </Select>

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
            <>
              <Input
                label="Brave Search API key"
                type="password"
                value={draft.api_key ?? ''}
                disabled={!isAdmin}
                onChange={(e) => updateDraft({ api_key: e.target.value })}
                placeholder={apiKeyConfigured ? 'API key configured — paste to replace' : 'BSA…'}
              />
              {apiKeyConfigured && isAdmin && (
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => updateDraft({ api_key: '' })}
                    disabled={saveMutation.isPending}
                  >
                    Clear stored key
                  </Button>
                </div>
              )}
            </>
          )}
        </div>

        <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-3 text-sm text-gray-600 dark:text-gray-300 flex items-center gap-2">
          <KeyRound size={15} className="text-primary shrink-0" />
          {isAdmin
            ? 'API keys are stored encrypted at rest.'
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
