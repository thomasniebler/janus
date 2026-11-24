import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, BellRing, Check, RefreshCw } from 'lucide-react';
import { emailApi } from '../../api';
import type { SecretarySettingsResponse as SecretarySettingsData } from '../../lib/schemas';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input, Select } from '../ui/Input';

const secretaryQueryKey = ['secretary', 'settings'] as const;

const START_HOURS = Array.from({ length: 24 }, (_, hour) => hour);
const END_HOURS = Array.from({ length: 24 }, (_, index) => index + 1);
const formatHour = (hour: number) => `${String(hour % 24).padStart(2, '0')}:00`;

const errorMessage = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    return error.message;
  }
  return 'Failed to save the secretary settings.';
};

const secretaryHeader = (
  <CardHeader>
    <div className="space-y-2">
      <h2 className="text-xl font-bold flex items-center gap-2">
        <BellRing size={24} className="text-primary" />
        Secretary
      </h2>
      <p className="text-sm text-gray-500 dark:text-gray-400">
        The Secretary is the built-in trigger (Settings → Triggers) that thinks ahead for you every hour and starts a
        conversation when something deserves your attention. It only runs during your waking hours, and skips an hour
        without calling a model when nothing changed.
      </p>
    </div>
  </CardHeader>
);

export const SecretarySettings: React.FC = () => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Partial<SecretarySettingsData>>({});
  const [feedback, setFeedback] = useState<{
    status: 'success' | 'error';
    message: string;
  } | null>(null);

  const {
    data: saved,
    isLoading,
    isError,
  } = useQuery({
    queryKey: secretaryQueryKey,
    queryFn: async () => (await emailApi.getSecretarySettings()).data,
  });

  const settings: SecretarySettingsData | undefined = saved ? { ...saved, ...draft } : undefined;

  const change = (patch: Partial<SecretarySettingsData>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setFeedback(null);
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!settings) throw new Error('Settings are not loaded yet.');
      return (
        await emailApi.updateSecretarySettings({
          secretary_active_start_hour: settings.secretary_active_start_hour,
          secretary_active_end_hour: settings.secretary_active_end_hour,
          secretary_whatsapp_enabled: settings.secretary_whatsapp_enabled,
          // An empty string clears the number (null is ignored by the API).
          secretary_whatsapp_number: (settings.secretary_whatsapp_number ?? '').trim(),
        })
      ).data;
    },
    onSuccess: (next) => {
      queryClient.setQueryData(secretaryQueryKey, next);
      setDraft({});
      setFeedback({ status: 'success', message: 'Secretary settings saved.' });
    },
    onError: (error) => setFeedback({ status: 'error', message: errorMessage(error) }),
  });

  if (isError) {
    return (
      <Card>
        {secretaryHeader}
        <CardContent>
          <div
            role="alert"
            className="p-4 rounded-2xl text-sm flex items-center gap-3 bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400"
          >
            <AlertCircle size={20} />
            <p className="font-medium">Could not load the secretary settings.</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (isLoading || !settings) {
    return (
      <Card>
        {secretaryHeader}
        <CardContent className="h-32 flex items-center justify-center">
          <RefreshCw className="animate-spin text-primary" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      {secretaryHeader}
      <CardContent className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Active from"
            aria-label="Active from"
            value={settings.secretary_active_start_hour}
            onChange={(event) =>
              change({
                secretary_active_start_hour: Number(event.target.value),
              })
            }
          >
            {START_HOURS.map((hour) => (
              <option key={hour} value={hour}>
                {formatHour(hour)}
              </option>
            ))}
          </Select>
          <Select
            label="Active until"
            aria-label="Active until"
            value={settings.secretary_active_end_hour}
            onChange={(event) => change({ secretary_active_end_hour: Number(event.target.value) })}
          >
            {END_HOURS.map((hour) => (
              <option key={hour} value={hour}>
                {formatHour(hour)}
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-3">
          <label className="flex items-center gap-3 text-sm font-medium text-gray-700 dark:text-gray-200">
            <input
              type="checkbox"
              checked={settings.secretary_whatsapp_enabled}
              onChange={(event) => change({ secretary_whatsapp_enabled: event.target.checked })}
              className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
            />
            WhatsApp alerts for time-critical items
          </label>
          <Input
            label="Your WhatsApp number"
            aria-label="Your WhatsApp number"
            placeholder="+4915112345678"
            value={settings.secretary_whatsapp_number ?? ''}
            onChange={(event) => change({ secretary_whatsapp_number: event.target.value })}
          />
          <p className="text-xs text-gray-500 dark:text-gray-400">
            With alerts on, the Secretary may send one short message to this number (at most three a day) when something
            needs you within about a day. It can message only this number, never anyone else, and it needs a connected
            WhatsApp account. Everything else arrives as a conversation in Janus.
          </p>
        </div>

        <Button onClick={() => saveMutation.mutate()} isLoading={saveMutation.isPending}>
          Save Secretary Settings
        </Button>

        {feedback && (
          <div
            role="status"
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
