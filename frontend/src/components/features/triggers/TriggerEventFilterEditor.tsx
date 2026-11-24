import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, Trash2, X } from 'lucide-react';
import { whatsappApi } from '../../../api';
import type { TriggerDefinition, TriggerEventSpec } from '../../../types';
import { Button } from '../../ui/Button';
import { Input } from '../../ui/Input';
import { formatTimestamp } from './triggerPresentation';
import {
  conversationSuggestions,
  describeEventFilter,
  MAX_CONDITION_VALUE_LENGTH,
  MAX_CONDITION_VALUES,
  MAX_EVENT_CONDITIONS,
  newConditionDraft,
  OP_LABELS,
  SUGGESTED_FIELDS,
  type EventConditionDraft,
  type TriggerEventConditionOp,
} from './triggerEventFilters';

/**
 * The "Only when" payload filter and the per-hour run limit of an
 * event-activated trigger definition (JAN-363), plus their read-only summary
 * for definition and proposal cards.
 */

const selectClass =
  'block w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm';
const labelClass = 'text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1';

interface ConditionValuesProps {
  /** 0-based condition index, for element ids. */
  index: number;
  values: string[];
  onChange: (values: string[]) => void;
  suggestions: { value: string; label: string }[];
}

/**
 * One condition's values as removable chips plus an input that adds its whole
 * trimmed text as a single value (Enter or "Add"). Nothing is split on commas,
 * so stored values round-trip unchanged.
 */
const ConditionValues: React.FC<ConditionValuesProps> = ({ index, values, onChange, suggestions }) => {
  const n = index + 1;
  const inputId = `trigger-filter-values-${index}`;
  const listId = `${inputId}-suggestions`;
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const full = values.length >= MAX_CONDITION_VALUES;
  const pending = text.trim();

  // Typed text that was never added would silently be lost on save, so the form refuses it.
  useEffect(() => {
    inputRef.current?.setCustomValidity(pending ? 'Press Enter or "Add" to add this value, or clear it.' : '');
  }, [pending]);

  const add = () => {
    if (!pending || full) return;
    if (!values.includes(pending)) onChange([...values, pending]);
    setText('');
  };

  return (
    <div className="space-y-1.5">
      <label className={labelClass} htmlFor={inputId}>
        Values<span className="sr-only"> of condition {n}</span>
      </label>
      {values.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label={`Condition ${n} values`}>
          {values.map((value) => (
            <li
              key={value}
              className="inline-flex max-w-full items-center gap-1 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800 py-0.5 pl-2.5 pr-1 text-xs text-gray-700 dark:text-gray-200"
            >
              <span className="truncate" title={value}>
                {value}
              </span>
              <button
                type="button"
                aria-label={`Remove ${value}`}
                onClick={() => onChange(values.filter((item) => item !== value))}
                className="rounded-full p-0.5 text-gray-400 hover:text-red-500 hover:bg-gray-200 dark:hover:bg-gray-700"
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <input
          ref={inputRef}
          id={inputId}
          value={text}
          required={values.length === 0}
          disabled={full}
          maxLength={MAX_CONDITION_VALUE_LENGTH}
          list={suggestions.length > 0 ? listId : undefined}
          placeholder={full ? `At most ${MAX_CONDITION_VALUES} values` : 'Type a value, press Enter'}
          onChange={(changeEvent) => setText(changeEvent.target.value)}
          onKeyDown={(keyEvent) => {
            if (keyEvent.key !== 'Enter') return;
            keyEvent.preventDefault();
            add();
          }}
          className={selectClass}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label={`Add value to condition ${n}`}
          disabled={!pending || full}
          onClick={add}
        >
          Add
        </Button>
      </div>
      {suggestions.length > 0 && (
        <datalist id={listId}>
          {suggestions.map((suggestion) => (
            <option key={suggestion.value} value={suggestion.value} label={suggestion.label} />
          ))}
        </datalist>
      )}
    </div>
  );
};

interface TriggerEventFilterEditorProps {
  /** The selected catalog event; null for an uncatalogued value; undefined renders nothing (not an event definition). */
  event: TriggerEventSpec | null | undefined;
  conditions: EventConditionDraft[];
  onConditionsChange: (conditions: EventConditionDraft[]) => void;
  /** The limit as typed; empty = no limit. */
  maxRuns: string;
  onMaxRunsChange: (text: string) => void;
}

export const TriggerEventFilterEditor: React.FC<TriggerEventFilterEditorProps> = ({
  event,
  conditions,
  onConditionsChange,
  maxRuns,
  onMaxRunsChange,
}) => {
  const fields = event?.payload ?? [];
  const eventType = event?.event_type ?? '';
  const isWhatsApp = eventType.startsWith('whatsapp.');
  const wantsSuggestions = isWhatsApp && conditions.some((condition) => SUGGESTED_FIELDS.has(condition.field));
  const { data: conversations = [] } = useQuery({
    queryKey: ['whatsapp-conversations', 'trigger-filter'],
    queryFn: async () => (await whatsappApi.getConversations(200)).data,
    enabled: wantsSuggestions,
    staleTime: 60_000,
  });
  const change = (index: number, changes: Partial<EventConditionDraft>) =>
    onConditionsChange(conditions.map((condition, i) => (i === index ? { ...condition, ...changes } : condition)));
  const add = () => onConditionsChange([...conditions, newConditionDraft(fields[0]?.name ?? '')]);
  const remove = (index: number) => onConditionsChange(conditions.filter((_, i) => i !== index));
  if (event === undefined) return null;

  return (
    <div className="space-y-4" data-testid="trigger-event-options">
      <fieldset className="space-y-2">
        <legend className={labelClass}>Only when</legend>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          No conditions: every event starts a run. Otherwise all conditions must match; comparisons ignore case.
          Add each value on its own.
        </p>
        {conditions.map((condition, index) => {
          const n = index + 1;
          const suggestions = isWhatsApp ? conversationSuggestions(eventType, condition.field, conversations) : [];
          return (
            <div key={condition.key} className="grid items-end gap-2 md:grid-cols-[1fr_1fr_2fr_auto]" data-testid={`trigger-filter-condition-${n}`}>
              <div className="space-y-1.5">
                <label className={labelClass} htmlFor={`trigger-filter-field-${index}`}>
                  Field<span className="sr-only"> of condition {n}</span>
                </label>
                <select
                  id={`trigger-filter-field-${index}`}
                  value={condition.field}
                  required
                  onChange={(changeEvent) => change(index, { field: changeEvent.target.value })}
                  className={selectClass}
                >
                  {fields.map((field) => (
                    <option key={field.name} value={field.name} title={field.description}>
                      {field.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className={labelClass} htmlFor={`trigger-filter-op-${index}`}>
                  Operator<span className="sr-only"> of condition {n}</span>
                </label>
                <select
                  id={`trigger-filter-op-${index}`}
                  value={condition.op}
                  onChange={(changeEvent) => change(index, { op: changeEvent.target.value as TriggerEventConditionOp })}
                  className={selectClass}
                >
                  {(Object.keys(OP_LABELS) as TriggerEventConditionOp[]).map((op) => (
                    <option key={op} value={op}>
                      {OP_LABELS[op]}
                    </option>
                  ))}
                </select>
              </div>
              <ConditionValues
                index={index}
                values={condition.values}
                onChange={(values) => change(index, { values })}
                suggestions={suggestions}
              />
              <Button
                type="button"
                size="sm"
                variant="ghost"
                icon={<Trash2 size={14} />}
                aria-label={`Remove condition ${n}`}
                onClick={() => remove(index)}
              />
            </div>
          );
        })}
        <Button
          type="button"
          size="sm"
          variant="outline"
          icon={<Plus size={14} />}
          disabled={fields.length === 0 || conditions.length >= MAX_EVENT_CONDITIONS}
          onClick={add}
        >
          Add condition
        </Button>
      </fieldset>
      <div className="space-y-1">
        <Input
          label="Max runs per hour"
          type="number"
          min={1}
          max={1000}
          step={1}
          placeholder="No limit"
          value={maxRuns}
          onChange={(changeEvent) => onMaxRunsChange(changeEvent.target.value)}
        />
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Empty means no limit. Events over the limit start no run and are counted on the trigger card.
        </p>
      </div>
    </div>
  );
};

/** Backend timestamps are UTC; some arrive without an explicit offset. */
const utc = (value: string): string => (/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);

type EventOptions = Pick<TriggerDefinition, 'kind' | 'event_filter' | 'max_runs_per_hour' | 'rate_limited_count' | 'last_rate_limited_at'>;

const describeLimits = (definition: EventOptions): string | null => {
  const parts = describeEventFilter(definition.event_filter);
  const condition = parts.length > 0 ? `Only when ${parts.join(' and ')}` : null;
  const limit = definition.max_runs_per_hour;
  const cap = limit == null ? null : `max ${limit} run${limit === 1 ? '' : 's'}/hour`;
  return [condition, cap].filter(Boolean).join(' · ') || null;
};

const describeSkipped = (definition: EventOptions): string | null => {
  const skipped = definition.rate_limited_count ?? 0;
  if (skipped === 0) return null;
  const last = definition.last_rate_limited_at ? formatTimestamp(utc(definition.last_rate_limited_at)) : null;
  return `${skipped} event${skipped === 1 ? '' : 's'} skipped by the rate limit${last ? ` (last: ${last})` : ''}`;
};

/** Read-only filter, limit and rate-limit drops of an event definition or proposal. */
export const TriggerEventOptionsSummary: React.FC<{ definition: EventOptions }> = ({ definition }) => {
  if (definition.kind !== 'event') return null;
  const limits = describeLimits(definition);
  const skipped = describeSkipped(definition);
  if (!limits && !skipped) return null;
  return (
    <div className="mt-1 space-y-0.5 text-xs text-gray-500 dark:text-gray-400" data-testid="trigger-event-options-summary">
      {limits && <p>{limits}</p>}
      {skipped && <p className="text-amber-600 dark:text-amber-400" data-testid="trigger-rate-limited">{skipped}</p>}
    </div>
  );
};
