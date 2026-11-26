import React from 'react';
import { DEFAULT_MAX_RUN_SECONDS, DEFAULT_MAX_TOOL_CALLS } from './triggerRunLimits';

type LimitMode = 'default' | 'custom' | 'off';

const modeOf = (value: number | null): LimitMode => (value === null ? 'default' : value === 0 ? 'off' : 'custom');

const selectClass =
  'px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm';

interface LimitFieldProps {
  label: string;
  unit: string;
  value: number | null;
  defaultValue: number;
  max: number;
  onChange: (value: number | null) => void;
}

const LimitField: React.FC<LimitFieldProps> = ({ label, unit, value, defaultValue, max, onChange }) => {
  const mode = modeOf(value);
  return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="min-w-[9rem]">{label}</span>
        <select
          aria-label={`${label} limit`}
          className={selectClass}
          value={mode}
          onChange={(event) => {
            const next = event.target.value as LimitMode;
            onChange(next === 'default' ? null : next === 'off' ? 0 : value || defaultValue);
          }}
        >
          <option value="default">Default ({defaultValue} {unit})</option>
          <option value="custom">Custom</option>
          <option value="off">No limit</option>
        </select>
        {mode === 'custom' && (
          <input
            type="number"
            aria-label={label}
            min={1}
            max={max}
            value={value ?? defaultValue}
            onChange={(event) => {
              const parsed = Number.parseInt(event.target.value, 10);
              onChange(Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, max) : defaultValue);
            }}
            className={`${selectClass} w-24`}
          />
        )}
        {mode === 'custom' && <span className="text-xs text-gray-500">{unit}</span>}
      </div>
    );
  };

  type RunLimitKey = 'max_tool_calls' | 'max_run_seconds';

  interface TriggerRunLimitsFieldsProps {
    limits: { max_tool_calls?: number | null; max_run_seconds?: number | null };
    onChange: (key: RunLimitKey, value: number | null) => void;
  }

  /** Per-definition bounds on one run: how many tool-calling rounds and how long, or no limit at all. */
  export const TriggerRunLimitsFields: React.FC<TriggerRunLimitsFieldsProps> = ({ limits, onChange }) => {
    const maxToolCalls = limits.max_tool_calls ?? null;
    const maxRunSeconds = limits.max_run_seconds ?? null;
    return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1">Run limits</legend>
      <LimitField
        label="Tool-calling rounds"
        unit="rounds"
        value={maxToolCalls}
        defaultValue={DEFAULT_MAX_TOOL_CALLS}
        max={500}
        onChange={(value) => onChange('max_tool_calls', value)}
      />
      <LimitField
        label="Run time"
        unit="seconds"
        value={maxRunSeconds}
        defaultValue={DEFAULT_MAX_RUN_SECONDS}
        max={86400}
        onChange={(value) => onChange('max_run_seconds', value)}
      />
      {(maxToolCalls === 0 || maxRunSeconds === 0) && (
        <p className="text-xs text-amber-500">
          Without a limit a run continues until the model finishes. Triggers run one at a time, so a long run delays
          the others (mail processing included), and every round costs model usage.
        </p>
      )}
    </fieldset>
  );
};
