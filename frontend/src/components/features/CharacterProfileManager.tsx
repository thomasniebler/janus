import React, { useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Check, Eye, RefreshCw, Save, Shield } from 'lucide-react';
import { characterApi } from '../../api';
import type { CharacterProfile, CharacterProfileUpdate } from '../../types';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader } from '../ui/Card';
import { Input } from '../ui/Input';

const characterQueryKey = ['character-profile'] as const;

type CharacterDraft = CharacterProfileUpdate & { version: number };

const splitLines = (value: string): string[] => value.split('\n').map((line) => line.trim()).filter(Boolean);

const toDraft = (profile: CharacterProfile): CharacterDraft => ({
  version: profile.version,
  name: profile.name,
  identity: profile.identity,
  core_values: [...profile.core_values],
  non_negotiables: [...profile.non_negotiables],
  traits: profile.traits.map((trait) => ({ ...trait })),
  additional_instructions: profile.additional_instructions ?? '',
  memory_instructions: profile.memory_instructions ?? '',
});

const textareaClass =
  'w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y';
const labelClass = 'text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1';

export const CharacterProfileManager: React.FC = () => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<CharacterDraft | null>(null);
  const [feedback, setFeedback] = useState<{ status: 'success' | 'error'; message: string } | null>(null);

  const { data: profile, isLoading, isError } = useQuery({
    queryKey: characterQueryKey,
    queryFn: async () => (await characterApi.get()).data,
  });

  const currentDraft = draft ?? (profile ? toDraft(profile) : null);

  const previewMutation = useMutation({
    mutationFn: (data: CharacterProfileUpdate) => characterApi.preview(data),
  });

  const saveMutation = useMutation({
    mutationFn: (data: CharacterProfileUpdate) => characterApi.update(data),
    onSuccess: (response) => {
      queryClient.setQueryData(characterQueryKey, response.data);
      setDraft(toDraft(response.data));
      setFeedback({ status: 'success', message: `Character constitution saved as version ${response.data.version}.` });
    },
    onError: (error) => {
      setFeedback({
        status: 'error',
        message: axios.isAxiosError(error) ? error.message : 'Failed to save the character constitution.',
      });
    },
  });

  if (isLoading || currentDraft === null) {
    return (
      <Card className="h-48 flex items-center justify-center">
        {isError ? <p className="text-sm text-red-300">Could not load the character constitution.</p> : <RefreshCw className="animate-spin text-primary" />}
      </Card>
    );
  }

  const update = <K extends keyof CharacterDraft>(key: K, value: CharacterDraft[K]) => {
    setDraft((current) => ({ ...(current ?? currentDraft), [key]: value }));
    setFeedback(null);
  };

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    const coreValues = splitLines(currentDraft.core_values.join('\n'));
    const nonNegotiables = splitLines(currentDraft.non_negotiables.join('\n'));
    if (!currentDraft.name.trim() || !currentDraft.identity.trim() || !coreValues.length || !nonNegotiables.length) {
      setFeedback({ status: 'error', message: 'Name, identity, at least one core value, and at least one boundary are required.' });
      return;
    }
    saveMutation.mutate(toPayload(coreValues, nonNegotiables));
  };

  function toPayload(coreValues: string[], nonNegotiables: string[]): CharacterProfileUpdate {
    const draftValue = currentDraft as CharacterDraft;
    return {
      name: draftValue.name.trim(),
      identity: draftValue.identity.trim(),
      core_values: coreValues,
      non_negotiables: nonNegotiables,
      traits: draftValue.traits,
      additional_instructions: draftValue.additional_instructions?.trim() || null,
      memory_instructions: draftValue.memory_instructions?.trim() || null,
    };
  }

  const showPreview = () => {
    const coreValues = splitLines(currentDraft.core_values.join('\n'));
    const nonNegotiables = splitLines(currentDraft.non_negotiables.join('\n'));
    if (!currentDraft.name.trim() || !currentDraft.identity.trim() || !coreValues.length || !nonNegotiables.length) {
      setFeedback({ status: 'error', message: 'Fill in name, identity, values, and boundaries to preview the prompt.' });
      return;
    }
    previewMutation.mutate(toPayload(coreValues, nonNegotiables));
  };

  return (
    <form onSubmit={save} className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Bot size={22} className="text-primary" />
            Character constitution
          </h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Shape how Janus presents itself while keeping authorization, safety, and user controls above adaptation.
          </p>
        </div>
        <Button type="submit" icon={<Save size={16} />} isLoading={saveMutation.isPending}>
          Save constitution
        </Button>
      </div>

      {feedback && (
        <div className={`flex items-center gap-2 rounded-xl border px-4 py-3 text-sm ${feedback.status === 'success' ? 'border-green-800 bg-green-900/20 text-green-300' : 'border-red-800 bg-red-900/20 text-red-300'}`}>
          {feedback.status === 'success' ? <Check size={16} /> : <Shield size={16} />}
          {feedback.message}
        </div>
      )}

      <Card>
        <CardHeader>
          <h3 className="font-bold">Stable identity</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">This constitution is user-owned and versioned. It does not grant tools or permissions.</p>
        </CardHeader>
        <CardContent className="space-y-4">
          <Input label="Name" value={currentDraft.name} onChange={(event) => update('name', event.target.value)} required />
          <div className="space-y-1.5">
            <label className="text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1" htmlFor="character-identity">
              Identity
            </label>
            <textarea
              id="character-identity"
              value={currentDraft.identity}
              onChange={(event) => update('identity', event.target.value)}
              rows={4}
              required
              className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <h3 className="font-bold">Values and boundaries</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Enter one value or boundary per line. These are stable constitution rules.</p>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          {(['core_values', 'non_negotiables'] as const).map((key) => (
            <div key={key} className="space-y-1.5">
              <label className="text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1" htmlFor={`character-${key}`}>
                {key === 'core_values' ? 'Core values' : 'Non-negotiables'}
              </label>
              <textarea
                id={`character-${key}`}
                value={currentDraft[key].join('\n')}
                onChange={(event) => update(key, splitLines(event.target.value))}
                rows={6}
                required
                className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <h3 className="font-bold">Additional instructions</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Free-form system-prompt text appended after your values and boundaries. It applies to chat and to every trigger
            run (briefing, digest, mail processing, your own automations).
          </p>
        </CardHeader>
        <CardContent>
          <label className="sr-only" htmlFor="character-additional-instructions">Additional instructions</label>
          <textarea
            id="character-additional-instructions"
            value={currentDraft.additional_instructions ?? ''}
            onChange={(event) => update('additional_instructions', event.target.value)}
            rows={8}
            placeholder="e.g. Answer in German unless I write in English. Keep summaries under 150 words."
            className={textareaClass}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <h3 className="font-bold">Memory instructions</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            After every chat turn Janus decides whether something is worth remembering as a memory fact. These rules replace
            the built-in ones; leave empty to use the defaults (shown in the preview below).
          </p>
        </CardHeader>
        <CardContent>
          <label className="sr-only" htmlFor="character-memory-instructions">Memory instructions</label>
          <textarea
            id="character-memory-instructions"
            value={currentDraft.memory_instructions ?? ''}
            onChange={(event) => update('memory_instructions', event.target.value)}
            rows={6}
            placeholder="- Remember stable preferences and ongoing projects.&#10;- Never remember health details."
            className={textareaClass}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="font-bold">Prompt preview</h3>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                The exact system prompt Janus would use with the current (unsaved) edits, including guardrails and skills.
                Trigger runs append their own short run guidance after it.
              </p>
            </div>
            <Button type="button" size="sm" variant="outline" icon={<Eye size={14} />} isLoading={previewMutation.isPending} onClick={showPreview}>
              Show preview
            </Button>
          </div>
        </CardHeader>
        {previewMutation.data && (
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <p className={labelClass}>System prompt</p>
              <pre data-testid="character-prompt-preview" className="max-h-96 overflow-auto whitespace-pre-wrap rounded-xl bg-gray-50 dark:bg-gray-900 p-3 text-xs">
                {previewMutation.data.data.system_prompt}
              </pre>
            </div>
            <div className="space-y-1.5">
              <p className={labelClass}>Effective memory rules</p>
              <pre className="whitespace-pre-wrap rounded-xl bg-gray-50 dark:bg-gray-900 p-3 text-xs">{previewMutation.data.data.memory_instructions}</pre>
            </div>
          </CardContent>
        )}
        {previewMutation.isError && <CardContent><p className="text-sm text-red-400">Could not render the preview.</p></CardContent>}
      </Card>

    </form>
  );
};
