import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ExternalLink, KeyRound, RefreshCw, Wifi } from 'lucide-react';
import { linearApi } from '../../api';
import type { LinearSettingsUpdate } from '../../types';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { FeedbackBanner, type Feedback } from '../ui/FeedbackBanner';
import { requestErrorMessage } from '../../lib/requestErrors';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Badge } from '../ui/Badge';

const LinearLogo: React.FC<{ size?: number; className?: string }> = ({ size = 24, className }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 100 100"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M1.228 51.448 48.552 98.772a49.817 49.817 0 0 1-47.324-47.324ZM0 41.24 58.76 100A50.06 50.06 0 0 1 50 100C22.386 100 0 77.614 0 50c0-2.94.254-5.82.74-8.62L0 41.24ZM6.614 22.376 77.624 93.386A49.817 49.817 0 0 1 22.376 6.614l1.228-1.228a50.003 50.003 0 0 0-17.226 6.228L6.614 22.376ZM16.736 9.752l73.512 73.512A49.815 49.815 0 0 1 16.736 9.752ZM27.38 4.22l68.4 68.4A49.815 49.815 0 0 1 27.38 4.22ZM41.24 0 100 58.76C97.56 27.01 72.99 2.44 41.24 0ZM51.448 1.228A49.817 49.817 0 0 1 98.772 48.552L51.448 1.228Z" />
  </svg>
);

// `null` means "leave unchanged" to the backend and `""` means "clear", so a cleared
// key or emptied team is sent as `""` (JAN-222: "Clear stored key" kept the key).
const buildPayload = (draft: LinearSettingsUpdate): LinearSettingsUpdate => {
  const payload: LinearSettingsUpdate = {};
  // Only "Clear stored key" (a `null` draft) clears the key; a key field typed in and
  // emptied again is left out, so it never wipes the stored key as a side effect.
  if (draft.api_key === null) payload.api_key = '';
  else if (draft.api_key?.trim()) payload.api_key = draft.api_key.trim();
  if ('default_team_id' in draft) payload.default_team_id = draft.default_team_id?.trim() ?? '';
  return payload;
};

/** Linear integration settings; instance-wide, so only an admin can change them (JAN-222). */
export const LinearConfig: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<LinearSettingsUpdate>({});
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['linear-settings'],
    queryFn: async () => (await linearApi.getSettings()).data,
  });

  const updateDraft = (changes: LinearSettingsUpdate) => {
    setDraft((current) => ({ ...current, ...changes }));
    setFeedback(null);
  };

  const saveMutation = useMutation({
    mutationFn: () => linearApi.updateSettings(buildPayload(draft)),
    onSuccess: (response) => {
      queryClient.setQueryData(['linear-settings'], response.data);
      setDraft({});
      setFeedback({ status: 'success', message: 'Linear settings saved.' });
    },
    onError: (error) => setFeedback({ status: 'error', message: requestErrorMessage(error, 'Failed to save Linear settings.') }),
  });

  const testMutation = useMutation({
    mutationFn: () => linearApi.testConnection(),
    onSuccess: (response) => {
      const result = response.data;
      setFeedback({
        status: result.status === 'success' ? 'success' : 'error',
        message: result.message,
      });
    },
    onError: (error) => setFeedback({ status: 'error', message: requestErrorMessage(error, 'Connection test failed.') }),
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <LinearLogo size={24} className="text-primary" />
            Linear
          </h2>
        </CardHeader>
        <CardContent className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  const apiKeyConfigured = settings?.api_key_configured ?? false;
  const apiKeyValue = draft.api_key ?? '';
  const defaultTeamIdValue =
    'default_team_id' in draft ? (draft.default_team_id ?? '') : (settings?.default_team_id ?? '');
  const hasApiKeyChange = 'api_key' in draft;
  const hasDefaultTeamIdChange = 'default_team_id' in draft;
  const canSave = hasApiKeyChange || hasDefaultTeamIdChange;
  const canTestConnection = apiKeyConfigured && !hasApiKeyChange;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <h2 className="text-xl font-bold flex items-center gap-2">
              <LinearLogo size={24} className="text-primary" />
              Linear
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Connect a Linear API key to submit ideas as issues directly from chat.
            </p>
          </div>
          {apiKeyConfigured && (
            <Badge variant="success" className="shrink-0">
              API key configured
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* Instructions */}
        <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 space-y-2 text-sm text-gray-600 dark:text-gray-300">
          <div className="flex items-center gap-2 font-bold">
            <KeyRound size={16} className="text-primary" />
            How to create a Linear API key
          </div>
          <ol className="list-decimal list-inside space-y-1 text-gray-500 dark:text-gray-400">
            <li>
              Go to{' '}
              <a
                href="https://linear.app/settings/api"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline hover:no-underline inline-flex items-center gap-0.5"
              >
                Linear → Settings → API
                <ExternalLink size={12} />
              </a>
            </li>
            <li>Create a new Personal API key and copy it.</li>
            <li>
              Paste the key below. Optionally set a default team for new issues — the team key shown in issue IDs
              (e.g. <code>ENG</code> for <code>ENG-123</code>) or the team UUID.
            </li>
          </ol>
        </div>

        {/* Input fields */}
        <div className="grid gap-4">
          <Input
            label="Personal API Key"
            type="password"
            disabled={!isAdmin}
            value={apiKeyValue}
            onChange={(e) => updateDraft({ api_key: e.target.value })}
            placeholder={apiKeyConfigured ? 'API key configured — paste to replace' : 'lin_api_…'}
          />
          {apiKeyConfigured && isAdmin && (
            <div className="flex justify-end">
              <Button
                type="button"
                variant="ghost"
                onClick={() => updateDraft({ api_key: null })}
                disabled={saveMutation.isPending}
              >
                Clear stored key
              </Button>
            </div>
          )}
          <Input
            label="Default Team (key or ID)"
            disabled={!isAdmin}
            value={defaultTeamIdValue}
            onChange={(e) => updateDraft({ default_team_id: e.target.value })}
            placeholder="e.g. ENG — the prefix of your issue IDs"
          />
        </div>

        {/* Storage indicator */}
        <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-3 text-sm text-gray-600 dark:text-gray-300 flex items-center gap-2">
          <KeyRound size={15} className="text-primary shrink-0" />
          {apiKeyConfigured ? 'API key is stored encrypted at rest.' : 'No API key has been stored yet.'}
          {!isAdmin && ' Only an administrator can change Linear settings.'}
        </div>

        {/* Actions */}
        <div className="flex flex-wrap items-center gap-3">
          {isAdmin && (
            <Button
              variant="secondary"
              onClick={() => saveMutation.mutate()}
              isLoading={saveMutation.isPending}
              disabled={!canSave}
              icon={<Check size={16} />}
            >
              Save Settings
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => testMutation.mutate()}
            isLoading={testMutation.isPending}
            disabled={!canTestConnection}
            icon={<Wifi size={16} />}
          >
            Test Connection
          </Button>
        </div>

        {hasApiKeyChange && (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Save Linear settings before testing the connection so Janus uses the stored key.
          </p>
        )}

        {/* Feedback */}
        <FeedbackBanner feedback={feedback} />
      </CardContent>
    </Card>
  );
};
