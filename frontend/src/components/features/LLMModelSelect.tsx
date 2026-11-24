import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { plannerApi } from '../../api';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import type { AzureOpenAIAuthMode } from '../../lib/llmProviders';
import type { LLMModelListResponse, LLMProviderId } from '../../types';
import { Combobox } from '../ui/Combobox';
import { Input } from '../ui/Input';

/** How long typing in the key/base-URL fields must pause before the model list is refetched. */
export const MODEL_LOOKUP_DEBOUNCE_MS = 400;

export const MODEL_LIST_FALLBACK_NOTE = 'Could not load model list — enter a model name manually.';

interface LLMModelSelectProps {
  provider: LLMProviderId;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Unsaved API key from the form; empty means "use the stored key". */
  apiKey?: string;
  /** Unsaved base URL from the form; omit for providers without one. Empty means the provider default. */
  baseUrl?: string;
  /** Azure OpenAI only (JAN-349): the draft auth mode, so Entra ID lists without a key. */
  authMode?: AzureOpenAIAuthMode;
  className?: string;
}

/** Entra ID sends no key: drop the (still debounced) key at once, so switching to Entra never sends it. */
const keyForLookup = (debouncedKey: string, authMode?: AzureOpenAIAuthMode) => (authMode === 'entra' ? '' : debouncedKey);

/**
 * Model picker for one LLM provider (JAN-215).
 *
 * Fetches the provider's models using the current draft credentials (without saving
 * them) and renders a searchable dropdown. If the lookup fails or returns nothing,
 * it falls back to a plain text input so the user is never blocked.
 */
export const LLMModelSelect: React.FC<LLMModelSelectProps> = ({
  provider,
  label,
  value,
  onChange,
  placeholder,
  apiKey = '',
  baseUrl,
  authMode,
  className,
}) => {
  // Reset immediately on a provider change instead of riding out the debounce with the
  // previous provider's credentials, which would otherwise send them to the new provider.
  const debouncedKey = useDebouncedValue(apiKey.trim(), MODEL_LOOKUP_DEBOUNCE_MS, provider);
  const debouncedBaseUrl = useDebouncedValue(baseUrl?.trim(), MODEL_LOOKUP_DEBOUNCE_MS, provider);
  const lookupKey = keyForLookup(debouncedKey, authMode);

  const { data, isPending, isError, isFetching } = useQuery({
    queryKey: ['llm-models', provider, lookupKey, debouncedBaseUrl ?? null, authMode ?? null],
    queryFn: async () =>
      (
        await plannerApi.listLLMModels({
          provider,
          api_key: lookupKey || undefined,
          base_url: debouncedBaseUrl,
          auth_mode: authMode,
        })
      ).data,
    staleTime: 5 * 60 * 1000,
    retry: false,
    // Keep showing the last list while a changed key/URL is re-checked, instead of flickering —
    // but only for another lookup of the *same* provider, so switching providers never leaves a
    // previous provider's models selectable while the new provider's list is still loading.
    placeholderData: (previousData: LLMModelListResponse | undefined, previousQuery) =>
      previousQuery?.queryKey[1] === provider ? previousData : undefined,
  });

  const loadingHint = (
    <span className="inline-flex items-center gap-1.5" role="status">
      <RefreshCw size={12} className="animate-spin" />
      Loading available models…
    </span>
  );

  if (isPending) {
    return (
      <Combobox
        label={label}
        value={value}
        onChange={onChange}
        options={[]}
        placeholder={placeholder}
        className={className}
        emptyMessage="Loading available models…"
        hint={loadingHint}
      />
    );
  }

  const models = data?.models ?? [];
  if (isError || models.length === 0) {
    const detail = isError ? null : data?.error;
    return (
      <div className={className}>
        <Input label={label} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
        <p className="mt-1.5 ml-1 text-xs text-gray-500 dark:text-gray-400">
          {MODEL_LIST_FALLBACK_NOTE}
          {detail ? <span className="block text-gray-400 dark:text-gray-500">{detail}</span> : null}
        </p>
      </div>
    );
  }

  return (
    <Combobox
      label={label}
      value={value}
      onChange={onChange}
      options={models.map((model) => ({ value: model.id, label: model.label }))}
      placeholder={placeholder}
      className={className}
      hint={
        isFetching
          ? loadingHint
          : `${models.length} ${models.length === 1 ? 'model' : 'models'} available — type to search, or enter any model id.`
      }
    />
  );
};
