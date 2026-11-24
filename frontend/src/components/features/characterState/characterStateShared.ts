import axios from 'axios';
import type { CharacterStateDimension, CharacterStateValues } from '../../../lib/characterState';

/** TanStack Query keys of the adaptive character state (JAN-301); the `character` domain invalidates them. */
export const characterStateQueryKey = ['character-state'] as const;
export const characterStateHistoryQueryKey = ['character-state-history'] as const;
export const characterStateProposalsQueryKey = ['character-state-proposals'] as const;

export const DIMENSIONS: { key: CharacterStateDimension; label: string; hint: string }[] = [
  { key: 'tone', label: 'Tone', hint: 'How replies sound' },
  { key: 'verbosity', label: 'Verbosity', hint: 'How long replies are' },
  { key: 'proactivity', label: 'Proactivity', hint: 'How readily Janus suggests next steps unasked' },
];

export const selectClass =
  'w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50';

/** A short, plain-text summary of (part of) a state: `tone warm · verbosity brief · focus "Q3 plan"`. */
export const describeValues = (values: Partial<CharacterStateValues>): string =>
  [
    ...DIMENSIONS.filter(({ key }) => key in values).map(({ key }) => `${key} ${values[key]}`),
    ...('current_focus' in values ? [values.current_focus ? `focus "${values.current_focus}"` : 'no focus'] : []),
  ].join(' · ');

/** The server's refusal reason (409 detail) when there is one. */
export const errorMessage = (error: unknown, fallback: string): string => {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
    return typeof detail === 'string' ? detail : error.message;
  }
  return fallback;
};
