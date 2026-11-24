import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, GitMerge, KeyRound, RefreshCw } from 'lucide-react';
import { selfEvolutionApi } from '../../lib/selfEvolution';
import type { SelfEvolutionSettings, SelfEvolutionSettingsUpdate } from '../../lib/selfEvolution';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Badge } from '../ui/Badge';

const ACTION_LABELS: Record<string, string> = {
  issue_filed: 'Issues filed',
  job_started: 'Implementation jobs',
  feature_merged: 'Feature merges',
  release_merged: 'Release merges',
  self_check: 'Self-checks',
  revert_opened: 'Revert PRs',
};

type Feedback = { status: 'success' | 'error'; message: string } | null;

const errorMessage = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : error.message;
  }
  return error instanceof Error ? error.message : 'Request failed.';
};

const header = (
  <h2 className="text-xl font-bold flex items-center gap-2">
    <GitMerge size={24} className="text-primary" />
    Self-evolution
  </h2>
);

/** Today's usage against the caps, the scope fence, and the newest recorded actions. */
const SelfEvolutionActivity: React.FC<{ settings: SelfEvolutionSettings }> = ({ settings }) => (
  <>
    <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-3 text-sm text-gray-600 dark:text-gray-300 space-y-1">
      <p className="font-medium">Used in the last 24 hours</p>
      <ul className="grid grid-cols-2 gap-x-4">
        {Object.entries(settings.daily_caps).map(([action, cap]) => (
          <li key={action}>
            {ACTION_LABELS[action] ?? action}: {settings.usage_last_24h[action] ?? 0} / {cap}
          </li>
        ))}
      </ul>
      <p className="text-xs text-gray-500">
        Never merged automatically: {settings.scope_fence.join(', ')}, and the loop's own files.
      </p>
    </div>
    {settings.recent_actions.length > 0 && (
      <div className="space-y-1 text-sm">
        <p className="font-medium">Recent actions</p>
        <ul className="space-y-1">
          {settings.recent_actions.map((item) => (
            <li key={item.id} className="flex flex-wrap gap-2 text-gray-600 dark:text-gray-300">
              <Badge variant={item.status === 'ok' || item.status === 'passed' ? 'success' : 'gray'}>{item.status}</Badge>
              <span>{ACTION_LABELS[item.action] ?? item.action}</span>
              <span className="font-mono">{item.subject}</span>
              {item.detail && <span className="text-gray-500 break-all">{item.detail}</span>}
            </li>
          ))}
        </ul>
      </div>
    )}
  </>
);

const FeedbackBanner: React.FC<{ feedback: Feedback }> = ({ feedback }) =>
  feedback && (
    <div
      className={`p-4 rounded-2xl text-sm flex items-center gap-3 ${
        feedback.status === 'success'
          ? 'bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400'
          : 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'
      }`}
    >
      {feedback.status === 'success' ? <Check size={20} /> : <AlertCircle size={20} />}
      <p className="font-medium">{feedback.message}</p>
    </div>
  );

/** The card while the settings load, or when loading them failed (with a retry). */
const SettingsLoadState: React.FC<{ error: unknown; retrying: boolean; onRetry: () => void }> = ({
  error,
  retrying,
  onRetry,
}) => (
  <Card>
    <CardHeader>{header}</CardHeader>
    {error ? (
      <CardContent className="space-y-4">
        <div role="alert" className="p-4 rounded-2xl text-sm flex items-center gap-3 bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle size={20} />
          <p className="font-medium">Could not load the self-evolution settings: {errorMessage(error)}</p>
        </div>
        <Button variant="secondary" onClick={onRetry} isLoading={retrying} icon={<RefreshCw size={16} />}>
          Retry
        </Button>
      </CardContent>
    ) : (
      <CardContent className="h-32 flex items-center justify-center">
        <RefreshCw className="animate-spin text-primary" aria-label="Loading self-evolution settings" />
      </CardContent>
    )}
  </Card>
);

/** Settings card for the opt-in autonomous self-evolution loop (JAN-320). */
export const SelfEvolutionConfig: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<SelfEvolutionSettingsUpdate>({});
  const [feedback, setFeedback] = useState<Feedback>(null);

  const { data: settings, error, isFetching, refetch } = useQuery({
    queryKey: ['self-evolution-settings'],
    queryFn: async () => (await selfEvolutionApi.getSettings()).data,
  });

  const updateDraft = (changes: SelfEvolutionSettingsUpdate) => {
    setDraft((current) => ({ ...current, ...changes }));
    setFeedback(null);
  };

  const saveMutation = useMutation({
    mutationFn: () => selfEvolutionApi.updateSettings(draft),
    onSuccess: (response) => {
      queryClient.setQueryData(['self-evolution-settings'], response.data);
      setDraft({});
      setFeedback({ status: 'success', message: 'Self-evolution settings saved.' });
    },
    onError: (error) => setFeedback({ status: 'error', message: errorMessage(error) }),
  });

  if (!settings) {
    return <SettingsLoadState error={error} retrying={isFetching} onRetry={() => void refetch()} />;
  }

  const field = (key: 'routine_trigger_id' | 'github_repo' | 'production_base_url') =>
    key in draft ? (draft[key] ?? '') : (settings[key] ?? '');

  return (
    <Card data-testid="self-evolution-card">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            {header}
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Let Janus file issues about its own gaps, start Claude Code implementation jobs, merge the resulting pull
              requests and releases, and verify production — without asking first. Only code-enforced gates (green
              CI, a scope fence, daily caps) stand in the way; every action is listed below and in the event log.
            </p>
          </div>
          <Badge variant={settings.enabled ? 'warning' : 'gray'} className="shrink-0">
            {settings.enabled ? 'On' : 'Off'}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        <label className="flex items-center gap-3 text-sm font-medium">
          <input
            type="checkbox"
            checked={draft.enabled ?? settings.enabled}
            disabled={!isAdmin}
            onChange={(e) => updateDraft({ enabled: e.target.checked })}
          />
          Run the self-evolution loop autonomously (no human review)
        </label>

        <div className="grid gap-4">
          <Input label="Claude Code Routine trigger id" value={field('routine_trigger_id')} disabled={!isAdmin} onChange={(e) => updateDraft({ routine_trigger_id: e.target.value })} placeholder="trig_…" />
          <Input label="Routine API token" type="password" value={draft.routine_token ?? ''} disabled={!isAdmin} onChange={(e) => updateDraft({ routine_token: e.target.value })} placeholder={settings.routine_token_configured ? 'Token configured — paste to replace' : 'sk-ant-oat01-…'} />
          <p className="text-xs text-gray-500 dark:text-gray-400">
            From the routine's API trigger on claude.ai (the token is shown once there). Janus sends each job's
            instructions as the fire payload, so the routine's own prompt must tell it to act on that payload.
          </p>
          <Input label="GitHub repository (owner/name; empty uses the GitHub default)" value={field('github_repo')} disabled={!isAdmin} onChange={(e) => updateDraft({ github_repo: e.target.value })} />
          <Input label="Production base URL for self-checks" value={field('production_base_url')} disabled={!isAdmin} onChange={(e) => updateDraft({ production_base_url: e.target.value })} placeholder="https://janus.example.org" />
        </div>

        <SelfEvolutionActivity settings={settings} />

        <div className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 p-3 text-sm text-gray-600 dark:text-gray-300 flex items-center gap-2">
          <KeyRound size={15} className="text-primary shrink-0" />
          {isAdmin
            ? 'The token is stored encrypted and never shown again.'
            : 'Only an administrator can configure self-evolution.'}
        </div>

        {isAdmin && (
          <Button
            variant="secondary"
            onClick={() => saveMutation.mutate()}
            isLoading={saveMutation.isPending}
            disabled={Object.keys(draft).length === 0}
            icon={<Check size={16} />}
          >
            Save Settings
          </Button>
        )}

        <FeedbackBanner feedback={feedback} />
      </CardContent>
    </Card>
  );
};
