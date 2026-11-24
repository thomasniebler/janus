import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, Layers } from 'lucide-react';
import { plannerApi } from '../../api';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { ModelChoiceFields } from './ModelChoiceFields';

type Choice = { provider: string | null; model: string | null };

/** Chat and utility model choices (JAN-233), next to the default AI backend settings. */
export const SurfaceModelConfig: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['system-settings'],
    queryFn: async () => (await plannerApi.getSettings()).data,
  });
  const [chat, setChat] = useState<Choice | null>(null);
  const [utility, setUtility] = useState<Choice | null>(null);
  const [feedback, setFeedback] = useState<{ status: 'success' | 'error'; message: string } | null>(null);

  const saveMutation = useMutation({
    mutationFn: (payload: { chat: Choice; utility: Choice }) =>
      plannerApi.updateSettings({
        chat_llm_provider: payload.chat.provider,
        chat_llm_model: payload.chat.model,
        utility_llm_provider: payload.utility.provider,
        utility_llm_model: payload.utility.model,
      }),
    onSuccess: (response) => {
      queryClient.setQueryData(['system-settings'], response.data);
      setChat(null);
      setUtility(null);
      setFeedback({ status: 'success', message: 'Model choices saved.' });
    },
    onError: (error) =>
      setFeedback({
        status: 'error',
        message: axios.isAxiosError(error) ? (error.response?.data?.detail ?? error.message) : 'Failed to save model choices.',
      }),
  });

  if (!settings) return null;
  const currentChat: Choice = chat ?? { provider: settings.chat_llm_provider ?? null, model: settings.chat_llm_model ?? null };
  const currentUtility: Choice = utility ?? {
    provider: settings.utility_llm_provider ?? null,
    model: settings.utility_llm_model ?? null,
  };

  return (
    <Card>
      <CardHeader>
        <h2 className="text-xl font-bold flex items-center gap-2">
          <Layers size={22} className="text-primary" />
          Models per surface
        </h2>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Chat and Janus&apos;s small helper calls can use a different model than the default. Each trigger picks its own model
          on Settings → Triggers.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        <ModelChoiceFields
          label="Chat"
          provider={currentChat.provider}
          model={currentChat.model}
          onChange={(provider, model) => {
            setChat({ provider, model });
            setFeedback(null);
          }}
          dataDescription="your chat messages and the tool results Janus reads"
        />
        <ModelChoiceFields
          label="Utility"
          provider={currentUtility.provider}
          model={currentUtility.model}
          onChange={(provider, model) => {
            setUtility({ provider, model });
            setFeedback(null);
          }}
          dataDescription="chat turns (for titles and memory suggestions) and memory facts (for compaction)"
        />
        <p className="text-xs text-gray-500 dark:text-gray-400">
          The utility model writes conversation titles, decides which chat facts to remember, and compacts memory. A small,
          cheap model is usually enough.
        </p>
        <Button onClick={() => saveMutation.mutate({ chat: currentChat, utility: currentUtility })} isLoading={saveMutation.isPending}>
          Save model choices
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
