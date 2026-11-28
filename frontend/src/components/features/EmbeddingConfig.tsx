import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, ChevronDown, ChevronRight, Cloud, Search } from 'lucide-react';
import { plannerApi } from '../../api';
import { unsupportedProviders } from '../../lib/aiProviders';
import type { EmbeddingProviderId } from '../../types';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input, Select } from '../ui/Input';

const PROVIDER_LABELS: Record<EmbeddingProviderId, string> = {
  ollama: 'Ollama (local)',
  openai: 'OpenAI',
  gemini: 'Google Gemini',
};

type Draft = { provider: EmbeddingProviderId | null; model: string; minSimilarity: string };

/** Backend default for `embedding_min_similarity`; any other stored value opens Advanced. */
const DEFAULT_MIN_SIMILARITY = 0.5;

/** Embedding model for semantic memory and topic search (JAN-241), next to the chat model settings. */
export const EmbeddingConfig: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['system-settings'],
    queryFn: async () => (await plannerApi.getSettings()).data,
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [feedback, setFeedback] = useState<{ status: 'success' | 'error'; message: string } | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState<boolean | null>(null);

  const saveMutation = useMutation({
    mutationFn: (payload: Draft) =>
      plannerApi.updateSettings({
        embedding_provider: payload.provider,
        embedding_model: payload.provider ? payload.model.trim() || null : null,
        embedding_min_similarity: Number(payload.minSimilarity),
      }),
    onSuccess: (response) => {
      queryClient.setQueryData(['system-settings'], response.data);
      setDraft(null);
      setFeedback({ status: 'success', message: 'Semantic search settings saved.' });
    },
    onError: (error) =>
      setFeedback({
        status: 'error',
        message: axios.isAxiosError(error)
          ? (error.response?.data?.detail ?? error.message)
          : 'Failed to save semantic search settings.',
      }),
  });

  if (!settings) return null;
  const current: Draft = draft ?? {
    provider: settings.embedding_provider ?? null,
    model: settings.embedding_model ?? '',
    minSimilarity: String(settings.embedding_min_similarity ?? 0.5),
  };
  const update = (next: Partial<Draft>) => {
    setDraft({ ...current, ...next });
    setFeedback(null);
  };
  const showAdvanced = advancedOpen ?? Number(current.minSimilarity) !== DEFAULT_MIN_SIMILARITY;
  const similarity = Number(current.minSimilarity);
  const similarityInvalid = current.minSimilarity.trim() === '' || Number.isNaN(similarity) || similarity < 0 || similarity > 1;

  return (
    <Card>
      <CardHeader>
        <h2 className="text-xl font-bold flex items-center gap-2">
          <Search size={22} className="text-primary" />
          Semantic search
        </h2>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          With an embedding model, memory and topic search find entries by meaning, not only by shared words. Without one,
          search matches keywords.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <Select
          label="Embedding provider"
          value={current.provider ?? ''}
          onChange={(event) => update({ provider: (event.target.value || null) as EmbeddingProviderId | null, model: '' })}
        >
          <option value="">Off (keyword search only)</option>
          {(Object.keys(PROVIDER_LABELS) as EmbeddingProviderId[]).map((id) => (
            <option key={id} value={id}>
              {PROVIDER_LABELS[id]}
            </option>
          ))}
          {unsupportedProviders(Object.keys(PROVIDER_LABELS)).map(([id, label]) => (
            <option key={id} value={id} disabled>
              {label} (no embeddings)
            </option>
          ))}
        </Select>
        {current.provider && (
          <>
            <Input
              label="Embedding model"
              value={current.model}
              placeholder={settings.embedding_default_models?.[current.provider] ?? ''}
              onChange={(event) => update({ model: event.target.value })}
            />
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Uses the {PROVIDER_LABELS[current.provider]} connection stored on the Models tab. Filters such as status and origin narrow
              the candidates first; similarity only orders them, and at most a few results come from meaning alone. Changing the
              model re-embeds every fact and topic in the background.
            </p>
            <button
              type="button"
              className="flex items-center gap-1 text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider"
              aria-expanded={showAdvanced}
              aria-controls="embedding-advanced"
              onClick={() => setAdvancedOpen(!showAdvanced)}
            >
              {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              Advanced
            </button>
            {showAdvanced && (
              <div id="embedding-advanced" className="space-y-2">
                <Input
                  label="Minimum similarity (0–1)"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={current.minSimilarity}
                  error={similarityInvalid ? 'Enter a number between 0 and 1' : undefined}
                  onChange={(event) => update({ minSimilarity: event.target.value })}
                />
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Drops meaning-only matches below this cosine similarity. Scores are not comparable across models and small
                  differences mean little, so leave the default unless you have measured your model.
                </p>
              </div>
            )}
            {current.provider !== 'ollama' && (
              <p className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400" data-testid="embedding-cloud-hint">
                <Cloud size={14} className="mt-0.5 shrink-0" />
                Cloud model: your memory facts, topics, and search queries will be sent to {PROVIDER_LABELS[current.provider]}.
              </p>
            )}
            {!settings.vector_search_available && (
              <p className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400" data-testid="vector-search-unavailable">
                <AlertCircle size={14} className="mt-0.5 shrink-0" />
                Vector search is unavailable on this server, so search stays keyword-only. The Status page shows why.
              </p>
            )}
          </>
        )}
        <Button onClick={() => saveMutation.mutate(current)} isLoading={saveMutation.isPending} disabled={similarityInvalid}>
          Save semantic search
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
