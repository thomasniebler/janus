import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ExternalLink, GitBranch, KeyRound, RefreshCw, Wifi } from 'lucide-react';
import { githubApi } from '../../api';
import type { GitHubSettingsUpdate } from '../../types';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { FeedbackBanner, type Feedback } from '../ui/FeedbackBanner';
import { requestErrorMessage } from '../../lib/requestErrors';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Badge } from '../ui/Badge';

// `null` means "leave unchanged" to the backend and `""` means "clear", so a cleared
// token or emptied repository is sent as `""` (JAN-222: "Clear stored token" kept the token).
// Only "Clear stored token" (a `null` draft) clears the token: a token field typed in and
// emptied again is left out, so it never wipes the stored token as a side effect.
const buildGitHubSettingsPayload = (draft: GitHubSettingsUpdate): GitHubSettingsUpdate => {
  const payload: GitHubSettingsUpdate = {};

  if (draft.token === null) {
    payload.token = '';
  } else if (draft.token?.trim()) {
    payload.token = draft.token.trim();
  }

  if ('default_repo' in draft) {
    payload.default_repo = draft.default_repo?.trim() ?? '';
  }

  return payload;
};

/** GitHub integration settings; instance-wide, so only an admin can change them (JAN-222). */
export const GitHubConfig: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<GitHubSettingsUpdate>({});
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['github-settings'],
    queryFn: async () => (await githubApi.getSettings()).data,
  });

  const updateDraft = (changes: GitHubSettingsUpdate) => {
    setDraft((current) => ({ ...current, ...changes }));
    setFeedback(null);
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      githubApi.updateSettings(buildGitHubSettingsPayload(draft)),
    onSuccess: (response) => {
      queryClient.setQueryData(['github-settings'], response.data);
      setDraft({});
      setFeedback({ status: 'success', message: 'GitHub settings saved.' });
    },
    onError: (error) => setFeedback({ status: 'error', message: requestErrorMessage(error, 'Failed to save GitHub settings.') }),
  });

  const testMutation = useMutation({
    mutationFn: () => githubApi.testConnection(),
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
            <GitBranch size={24} className="text-primary" />
            GitHub
          </h2>
        </CardHeader>
        <CardContent className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  const tokenConfigured = settings?.token_configured ?? false;
  const tokenValue = draft.token ?? '';
  const defaultRepoValue =
    'default_repo' in draft ? (draft.default_repo ?? '') : (settings?.default_repo ?? '');
  const hasTokenChange = 'token' in draft;
  const hasDefaultRepoChange = 'default_repo' in draft;
  const canSave = hasTokenChange || hasDefaultRepoChange;
  const canTestConnection = tokenConfigured && !hasTokenChange;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <h2 className="text-xl font-bold flex items-center gap-2">
              <GitBranch size={24} className="text-primary" />
              GitHub
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Connect a GitHub Personal Access Token to submit development ideas as issues from chat.
            </p>
          </div>
          {tokenConfigured && (
            <Badge variant="success" className="shrink-0">
              Token configured
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* Instructions */}
        <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 space-y-2 text-sm text-gray-600 dark:text-gray-300">
          <div className="flex items-center gap-2 font-bold">
            <KeyRound size={16} className="text-primary" />
            How to create a Personal Access Token
          </div>
          <ol className="list-decimal list-inside space-y-1 text-gray-500 dark:text-gray-400">
            <li>
              Go to{' '}
              <a
                href="https://github.com/settings/tokens/new"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline hover:no-underline inline-flex items-center gap-0.5"
              >
                GitHub → Settings → Developer settings → Personal access tokens
                <ExternalLink size={12} />
              </a>
            </li>
            <li>
              Generate a <strong>Fine-grained token</strong> (recommended) or a classic token with the{' '}
              <code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">repo</code> scope.
            </li>
            <li>
              For a fine-grained token, grant <strong>Pull requests: Read and write</strong> and{' '}
              <strong>Contents: Read and write</strong> (needed to merge), plus <strong>Issues: Read and write</strong>{' '}
              to file issues from chat, on the target repository. The self-evolution loop also needs{' '}
              <strong>Checks: Read</strong> and <strong>Commit statuses: Read</strong>. A classic token with the{' '}
              <code className="text-xs bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">repo</code> scope covers all
              of these.
            </li>
            <li>Paste the token below and optionally set a default repository.</li>
          </ol>
        </div>

        {/* Input fields */}
        <div className="grid gap-4">
          <Input
            label="Personal Access Token"
            type="password"
            disabled={!isAdmin}
            value={tokenValue}
            onChange={(e) => {
              updateDraft({ token: e.target.value });
            }}
            placeholder={tokenConfigured ? 'Token configured — paste to replace' : 'ghp_…'}
          />
          {tokenConfigured && isAdmin && (
            <div className="flex justify-end">
              <Button
                type="button"
                variant="ghost"
                onClick={() => updateDraft({ token: null })}
                disabled={saveMutation.isPending}
              >
                Clear stored token
              </Button>
            </div>
          )}
          <Input
            label="Default Repository"
            disabled={!isAdmin}
            value={defaultRepoValue}
            onChange={(e) => {
              updateDraft({ default_repo: e.target.value });
            }}
            placeholder="owner/repository-name"
          />
        </div>

        {/* Storage indicator */}
        <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-3 text-sm text-gray-600 dark:text-gray-300 flex items-center gap-2">
          <KeyRound size={15} className="text-primary shrink-0" />
          {tokenConfigured ? 'Token is stored encrypted at rest.' : 'No token has been stored yet.'}
          {!isAdmin && ' Only an administrator can change GitHub settings.'}
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

        {hasTokenChange && (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Save GitHub settings before testing the connection so Janus uses the stored token.
          </p>
        )}

        {/* Feedback */}
        <FeedbackBanner feedback={feedback} />
      </CardContent>
    </Card>
  );
};
