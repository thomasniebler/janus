import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Mail, RefreshCw } from 'lucide-react';
import { mailIngestionApi } from '../../api';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';
import { FeedbackBanner, type Feedback } from '../ui/FeedbackBanner';
import { requestErrorMessage } from '../../lib/requestErrors';
import type { MailIngestionSettings, MailIngestionSettingsUpdate } from '../../types';

type DraftSettings = {
  initial_sync_email_limit: string;
};

const buildDraftFromSettings = (settings: MailIngestionSettings): DraftSettings => ({
  initial_sync_email_limit: String(settings.initial_sync_email_limit),
});

const toPayload = (draft: DraftSettings): MailIngestionSettingsUpdate => ({
  initial_sync_email_limit: Number.parseInt(draft.initial_sync_email_limit, 10),
});

/** Instance-wide mail ingestion limits; only an admin can change them (JAN-222). */
export const MailIngestionConfig: React.FC<{ isAdmin: boolean }> = ({ isAdmin }) => {
  const queryClient = useQueryClient();
  const [draftOverrides, setDraftOverrides] = useState<Partial<DraftSettings>>({});
  const [result, setResult] = useState<Feedback | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['mail-ingestion-settings'],
    queryFn: async () => (await mailIngestionApi.getSettings()).data,
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
    mutationFn: (payload: MailIngestionSettingsUpdate) => mailIngestionApi.updateSettings(payload),
    onSuccess: (response) => {
      queryClient.setQueryData(['mail-ingestion-settings'], response.data);
      queryClient.invalidateQueries({ queryKey: ['system-settings'] });
      setDraftOverrides({});
      setResult({ status: 'success', message: 'Mail ingestion settings saved.' });
    },
    onError: (error) =>
      setResult({ status: 'error', message: requestErrorMessage(error, 'Failed to save mail ingestion settings.') }),
  });

  const updateDraft = <K extends keyof DraftSettings>(key: K, value: DraftSettings[K]) => {
    setDraftOverrides((current) => ({ ...current, [key]: value }));
    setResult(null);
  };

  if (isLoading || !settings || !draft) {
    return (
      <Card>
        <CardHeader>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Mail size={24} className="text-primary" />
            Mail Ingestion
          </h2>
        </CardHeader>
        <CardContent className="h-40 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  const payload = toPayload(draft);
  const parsedLimit = Number.parseInt(draft.initial_sync_email_limit, 10);
  const isValidLimit = Number.isInteger(parsedLimit) && parsedLimit > 0;

  return (
    <Card>
      <CardHeader>
        <div className="space-y-2">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Mail size={24} className="text-primary" />
            Mail Ingestion
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Control first-time mailbox catch-up for new mail accounts.
          </p>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2">
          <Input
            label="Initial Sync Email Limit"
            type="number"
            min={1}
            step={1}
            disabled={!isAdmin}
            value={draft.initial_sync_email_limit}
            onChange={(event) => updateDraft('initial_sync_email_limit', event.target.value)}
          />
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-2xl border border-gray-200 dark:border-gray-800 p-4 space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-gray-400">Mail sync behavior</p>
            <p className="text-sm text-gray-600 dark:text-gray-300">
              Janus applies the sync limit only when an account has no saved UID yet or needs a full mailbox catch-up.
            </p>
          </div>
          <div className="rounded-2xl border border-gray-200 dark:border-gray-800 p-4 space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-gray-400">Classifier behavior</p>
            <p className="text-sm text-gray-600 dark:text-gray-300">
              Janus guides new-mail triage with your Character settings and skills, while still enforcing the
              structured IMPORTANT vs DIGEST classifier.
            </p>
          </div>
        </div>

        {isAdmin ? (
          <div className="flex items-center gap-3">
            <Button
              onClick={() => saveMutation.mutate(payload)}
              isLoading={saveMutation.isPending}
              disabled={!isValidLimit}
            >
              Save Mail Ingestion Settings
            </Button>
            {!isValidLimit && <span className="text-sm text-red-500">Enter a whole number greater than 0.</span>}
          </div>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">Only an administrator can change mail ingestion settings.</p>
        )}

        <FeedbackBanner feedback={result} />
      </CardContent>
    </Card>
  );
};
