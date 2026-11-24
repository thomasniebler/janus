import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, Cloud, ImageIcon } from 'lucide-react';
import { plannerApi } from '../../api';
import type { ImageProviderId } from '../../lib/imageGeneration';
import type { PlannerSettings } from '../../types';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input, Select } from '../ui/Input';

const PROVIDER_LABELS: Record<ImageProviderId, string> = {
  openai: 'OpenAI',
  gemini: 'Google Gemini',
  openrouter: 'OpenRouter',
};

const KEY_CONFIGURED: Record<ImageProviderId, keyof PlannerSettings> = {
  openai: 'openai_api_key_configured',
  gemini: 'gemini_api_key_configured',
  openrouter: 'openrouter_api_key_configured',
};

type Draft = { provider: ImageProviderId | null; model: string };

/** Cloud image model for the chat tool generate_image (JAN-253), next to the other model settings. */
export const ImageGenerationConfig: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['system-settings'],
    queryFn: async () => (await plannerApi.getSettings()).data,
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [feedback, setFeedback] = useState<{ status: 'success' | 'error'; message: string } | null>(null);

  const saveMutation = useMutation({
    mutationFn: (payload: Draft) =>
      plannerApi.updateSettings({
        image_provider: payload.provider,
        image_model: payload.provider ? payload.model.trim() || null : null,
      }),
    onSuccess: (response) => {
      queryClient.setQueryData(['system-settings'], response.data);
      setDraft(null);
      setFeedback({ status: 'success', message: 'Image generation settings saved.' });
    },
    onError: (error) =>
      setFeedback({
        status: 'error',
        message: axios.isAxiosError(error)
          ? (error.response?.data?.detail ?? error.message)
          : 'Failed to save image generation settings.',
      }),
  });

  if (!settings) return null;
  const current: Draft = draft ?? { provider: settings.image_provider ?? null, model: settings.image_model ?? '' };
  const update = (next: Partial<Draft>) => {
    setDraft({ ...current, ...next });
    setFeedback(null);
  };
  const keyMissing = current.provider !== null && !settings[KEY_CONFIGURED[current.provider]];

  return (
    <Card>
      <CardHeader>
        <h2 className="text-xl font-bold flex items-center gap-2">
          <ImageIcon size={22} className="text-primary" />
          Image generation
        </h2>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Lets Janus draw pictures in chat when you ask for one. Images are made by a cloud model; Ollama and Anthropic
          cannot generate images.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <Select
          label="Image provider"
          value={current.provider ?? ''}
          onChange={(event) => update({ provider: (event.target.value || null) as ImageProviderId | null, model: '' })}
        >
          <option value="">Off (no image generation)</option>
          {(Object.keys(PROVIDER_LABELS) as ImageProviderId[]).map((id) => (
            <option key={id} value={id}>
              {PROVIDER_LABELS[id]}
            </option>
          ))}
        </Select>
        {current.provider && (
          <>
            <Input
              label="Image model"
              value={current.model}
              placeholder={settings.image_default_models?.[current.provider] ?? ''}
              onChange={(event) => update({ model: event.target.value })}
            />
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Uses the {PROVIDER_LABELS[current.provider]} key configured above. Each image costs money on that account and
              is kept with its conversation until you delete the conversation; each user may store up to{' '}
              {settings.image_storage_limit_mb ?? 200} MB of images.
            </p>
            <p className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400" data-testid="image-cloud-hint">
              <Cloud size={14} className="mt-0.5 shrink-0" />
              Cloud model: the prompt Janus writes for each image is sent to {PROVIDER_LABELS[current.provider]}.
            </p>
            {keyMissing && (
              <p className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400" data-testid="image-key-missing">
                <AlertCircle size={14} className="mt-0.5 shrink-0" />
                No {PROVIDER_LABELS[current.provider]} API key is stored yet, so Janus cannot generate images until you add one
                in AI Configuration.
              </p>
            )}
          </>
        )}
        <Button onClick={() => saveMutation.mutate(current)} isLoading={saveMutation.isPending}>
          Save image generation
        </Button>
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
