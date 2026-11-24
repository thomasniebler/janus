import React, { useMemo, useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, Mail, RefreshCw, Sparkles } from 'lucide-react';
import { dashboardApi } from '../../api';
import type { BriefingEmailWindow, DashboardWidgetPreference } from '../../types';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Select } from '../ui/Input';

const dashboardWidgetQueryKey = ['dashboard', 'widgets'] as const;

const defaultBriefingPreference: DashboardWidgetPreference = {
  widget_key: 'briefing',
  is_visible: true,
  is_collapsed: false,
  prompt_override: null,
  briefing_email_window: null,
};

const EMAIL_WINDOW_OPTIONS: Array<{ value: BriefingEmailWindow; label: string; description: string }> = [
  { value: 'today', label: 'Today', description: 'Summarize all mail received since midnight.' },
  { value: 'yesterday', label: 'Yesterday', description: 'Summarize all mail received during the previous day.' },
  { value: 'last_24_hours', label: 'Last 24 hours', description: 'Use a rolling one-day lookback window.' },
];

export const BriefingSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const [draftOverrides, setDraftOverrides] = useState<{
    prompt_override?: string;
    briefing_email_window?: BriefingEmailWindow | '';
  }>({});
  const [feedback, setFeedback] = useState<{ status: 'success' | 'error'; message: string } | null>(null);

  const { data: widgetPreferences, isLoading } = useQuery({
    queryKey: dashboardWidgetQueryKey,
    queryFn: async () => (await dashboardApi.getWidgets()).data,
  });

  const briefingPreference = useMemo(
    () => widgetPreferences?.find((preference) => preference.widget_key === 'briefing') ?? defaultBriefingPreference,
    [widgetPreferences]
  );

  const promptDraft = draftOverrides.prompt_override ?? briefingPreference.prompt_override ?? '';
  const emailWindowDraft = draftOverrides.briefing_email_window ?? briefingPreference.briefing_email_window ?? '';

  const saveMutation = useMutation({
    mutationFn: async () =>
      (
        await dashboardApi.updateWidgets([
          {
            widget_key: 'briefing',
            prompt_override: promptDraft.trim() ? promptDraft.trim() : null,
            briefing_email_window: emailWindowDraft || null,
          },
        ])
      ).data,
    onSuccess: (preferences) => {
      queryClient.setQueryData(dashboardWidgetQueryKey, preferences);
      setDraftOverrides({});
      setFeedback({ status: 'success', message: 'Daily briefing settings saved.' });
    },
    onError: (error) =>
      setFeedback({
        status: 'error',
        message: axios.isAxiosError(error) ? error.message : 'Failed to save daily briefing settings.',
      }),
  });

  const resetToDefaults = () => {
    setDraftOverrides({
      prompt_override: '',
      briefing_email_window: '',
    });
    setFeedback(null);
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Sparkles size={24} className="text-primary" />
            Daily Briefing
          </h2>
        </CardHeader>
        <CardContent className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="space-y-2">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Sparkles size={24} className="text-primary" />
            Daily Briefing
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            The briefing is the built-in <strong>Morning briefing</strong> trigger (Settings → Triggers), where you edit its
            main prompt, tools, and schedule. These settings add emphasis and optional email context on top.
          </p>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 text-sm text-gray-600 dark:text-gray-300">
          <div className="flex items-center gap-2 font-bold mb-2">
            <Mail size={16} className="text-primary" />
            How this works
          </div>
          <p>
            Janus writes the briefing in the voice of your Character settings. Each run gets today&apos;s planner summary
            as context; the extra instructions below are appended to it. If you choose an email window, the run also
            sees a bounded list of emails from that window.
          </p>
        </div>

        <div className="space-y-2">
          <label htmlFor="briefing-prompt" className="block text-xs font-bold uppercase tracking-wider text-gray-400 ml-1">
            Additional instructions
          </label>
          <textarea
            id="briefing-prompt"
            value={promptDraft}
            onChange={(event) => {
              setDraftOverrides((current) => ({
                ...current,
                prompt_override: event.target.value,
              }));
              setFeedback(null);
            }}
            rows={10}
            className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 shadow-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 dark:border-gray-700 dark:bg-dark-bg dark:text-gray-100"
            placeholder="Example: Lead with blockers, then highlight important follow-ups."
          />
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Leave this blank to use only Janus&apos;s default briefing prompt.
          </p>
        </div>

        <div className="space-y-3">
          <Select
            label="Email summary window"
            aria-label="Email summary window"
            value={emailWindowDraft}
            onChange={(event) => {
              setDraftOverrides((current) => ({
                ...current,
                briefing_email_window: event.target.value as BriefingEmailWindow | '',
              }));
              setFeedback(null);
            }}
          >
            <option value="">Disabled</option>
            {EMAIL_WINDOW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {emailWindowDraft
              ? EMAIL_WINDOW_OPTIONS.find((option) => option.value === emailWindowDraft)?.description
              : 'Keep email summaries out of the briefing context.'}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" onClick={resetToDefaults} disabled={!promptDraft && !emailWindowDraft}>
            Use defaults
          </Button>
          <Button onClick={() => saveMutation.mutate()} isLoading={saveMutation.isPending}>
            Save Briefing Settings
          </Button>
        </div>

        {feedback && (
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
        )}
      </CardContent>
    </Card>
  );
};
