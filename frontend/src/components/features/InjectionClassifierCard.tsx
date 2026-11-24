import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, ShieldCheck } from 'lucide-react';
import { injectionClassifierApi } from '../../lib/injectionClassifier';
import type { InjectionClassifierStatus } from '../../lib/injectionClassifier';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';

const QUERY_KEY = ['injection-classifier'];

const formatTokens = (value: number) => value.toLocaleString('en-US');

const errorMessage = (error: unknown, fallback: string) =>
  axios.isAxiosError(error) ? (error.response?.data?.detail ?? error.message) : fallback;

/**
 * The optional, model-based prompt-injection check (JAN-289): an opt-in second look by the
 * utility model at outside text the built-in rules found clean but that looks borderline.
 */
export const InjectionClassifierCard: React.FC = () => {
  const queryClient = useQueryClient();
  const [budgetDraft, setBudgetDraft] = useState<string | null>(null);
  const { confirm, dialog } = useConfirmDialog();
  const { data: status, isLoading, isError } = useQuery<InjectionClassifierStatus>({
    queryKey: QUERY_KEY,
    queryFn: async () => (await injectionClassifierApi.getStatus()).data,
  });
  const mutation = useMutation({
    mutationFn: (payload: { enabled?: boolean; monthly_token_budget?: number }) =>
      injectionClassifierApi.update(payload),
    onMutate: () => queryClient.cancelQueries({ queryKey: QUERY_KEY }),
    onSuccess: (response) => {
      queryClient.setQueryData(QUERY_KEY, response.data);
      setBudgetDraft(null);
    },
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent>
          <p className="text-sm text-gray-400">Loading the prompt-injection check…</p>
        </CardContent>
      </Card>
    );
  }
  if (isError || !status) {
    return (
      <Card>
        <CardContent>
          <p className="text-sm text-red-400 flex items-center gap-2" role="alert">
            <AlertCircle size={16} />
            Could not load the prompt-injection check settings.
          </p>
        </CardContent>
      </Card>
    );
  }

  const onToggle = (enabled: boolean) => {
    if (enabled && status.is_cloud) {
      confirm({
        title: 'Send suspicious outside text to a cloud model?',
        message:
          `With this on, mail, web, and other outside text that looks borderline is sent to ${status.model} ` +
          'for a second opinion. Turn it on?',
        confirmLabel: 'Turn on',
        tone: 'warning',
        onConfirm: () => mutation.mutate({ enabled }),
      });
      return;
    }
    mutation.mutate({ enabled });
  };
  const budgetValue = budgetDraft ?? String(status.monthly_token_budget);
  const budgetNumber = Number(budgetValue);
  const budgetValid = budgetValue.trim() !== '' && Number.isInteger(budgetNumber) && budgetNumber >= 0;

  return (
    <Card data-testid="injection-classifier-card">
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck size={17} />
              <h2 className="text-lg font-bold">Model-based prompt-injection check</h2>
            </div>
            <p className="mt-1 text-sm text-gray-400">
              Janus always checks outside text (mail, web pages, chats, tickets) with built-in rules. With this on,
              text the rules find clean but that looks borderline also gets a second look from {status.model}. It
              can only add a warning, never remove one, and when the model fails Janus keeps the rules' result.
              Each text is checked once.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm font-medium shrink-0">
            <input
              type="checkbox"
              aria-label="Model-based prompt-injection check"
              checked={status.enabled}
              disabled={mutation.isPending}
              onChange={(event) => onToggle(event.target.checked)}
            />
            {status.enabled ? 'On' : 'Off'}
          </label>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {status.is_cloud && (
          <p className="text-xs rounded-xl p-3 bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
            {status.model} is a cloud model: with this on, borderline outside text is sent to that provider. Choose an
            Ollama model under Settings → AI &amp; System (utility model) to keep it local.
          </p>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <p className="text-sm">
            Used in {status.period}: {formatTokens(status.tokens_used)} of {formatTokens(status.monthly_token_budget)}{' '}
            tokens · {status.calls} checks · {formatTokens(status.cached_verdicts)} remembered results
          </p>
          <form
            className="flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (budgetValid) mutation.mutate({ monthly_token_budget: budgetNumber });
            }}
          >
            <Input
              label="Monthly token budget"
              type="number"
              min={0}
              step={1000}
              value={budgetValue}
              onChange={(event) => setBudgetDraft(event.target.value)}
              error={budgetValid ? undefined : 'Enter a whole number of tokens.'}
            />
            <Button type="submit" disabled={!budgetValid || budgetDraft === null} isLoading={mutation.isPending}>
              Save
            </Button>
          </form>
        </div>
        {mutation.isError && (
          <p className="text-sm text-red-400" role="alert">
            {errorMessage(mutation.error, 'Saving failed.')}
          </p>
        )}
      </CardContent>
      {dialog}
    </Card>
  );
};
