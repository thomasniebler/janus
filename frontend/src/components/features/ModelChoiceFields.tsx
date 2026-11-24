import React from 'react';
import { Cloud } from 'lucide-react';
import type { LLMProviderId } from '../../types';
import { LLMModelSelect } from './LLMModelSelect';

const PROVIDER_LABELS: Record<LLMProviderId, string> = {
  ollama: 'Ollama',
  openai: 'OpenAI',
  openrouter: 'OpenRouter',
  gemini: 'Gemini',
  anthropic: 'Anthropic',
  github_models: 'GitHub Models',
  azure_openai: 'Azure OpenAI',
};

const selectClass =
  'block w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm';

interface ModelChoiceFieldsProps {
  /** Short label for the surface, e.g. "Trigger", "Chat", "Utility". */
  label: string;
  provider: string | null | undefined;
  model: string | null | undefined;
  onChange: (provider: string | null, model: string | null) => void;
  /** What this surface sends to the model, for the cloud hint. */
  dataDescription: string;
}

/**
 * Optional provider/model override for one surface (JAN-233).
 *
 * "Default model" keeps whatever Settings -> AI & System uses. An explicit
 * choice is never silently replaced by the default: if it is unusable, the
 * run or chat fails with an error that names it.
 */
export const ModelChoiceFields: React.FC<ModelChoiceFieldsProps> = ({ label, provider, model, onChange, dataDescription }) => {
  const selectId = `model-choice-${label.toLowerCase().replace(/\s+/g, '-')}`;
  const chosen = (provider || null) as LLMProviderId | null;
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1" htmlFor={selectId}>
          {label} model
        </label>
        <select
          id={selectId}
          value={chosen ?? ''}
          onChange={(event) => onChange(event.target.value || null, null)}
          className={selectClass}
        >
          <option value="">Default model (Settings → AI &amp; System)</option>
          {(Object.keys(PROVIDER_LABELS) as LLMProviderId[]).map((id) => (
            <option key={id} value={id}>
              {PROVIDER_LABELS[id]}
            </option>
          ))}
        </select>
      </div>
      {chosen && (
        <LLMModelSelect
          provider={chosen}
          label={`${PROVIDER_LABELS[chosen]} model for ${label.toLowerCase()}`}
          value={model ?? ''}
          onChange={(value) => onChange(chosen, value || null)}
          placeholder={
            chosen === 'azure_openai'
              ? 'Deployment name; leave empty for the configured deployment'
              : "Leave empty for this provider's configured model"
          }
        />
      )}
      {chosen && chosen !== 'ollama' && (
        <p className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400" data-testid={`${selectId}-cloud-hint`}>
          <Cloud size={14} className="mt-0.5 shrink-0" />
          Cloud model: {dataDescription} will be sent to {PROVIDER_LABELS[chosen]}.
        </p>
      )}
      {chosen && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          If this model cannot be used (for example, no API key is stored for {PROVIDER_LABELS[chosen]}), the run fails with an
          error instead of falling back to the default model.
        </p>
      )}
    </div>
  );
};
