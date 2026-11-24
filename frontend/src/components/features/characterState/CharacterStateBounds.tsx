import React, { useState } from 'react';
import { Save, SlidersHorizontal } from 'lucide-react';
import { CHARACTER_PROACTIVITY, CHARACTER_TONES, CHARACTER_VERBOSITY } from '../../../lib/characterState';
import type { CharacterStateDimension, CharacterStateEnvelope } from '../../../lib/characterState';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { DIMENSIONS, selectClass } from './characterStateShared';

const OPTIONS: Record<CharacterStateDimension, readonly string[]> = {
  tone: CHARACTER_TONES,
  verbosity: CHARACTER_VERBOSITY,
  proactivity: CHARACTER_PROACTIVITY,
};

type DimensionBounds = { default: string; allowed: string[] };

interface CharacterStateBoundsProps {
  envelope: CharacterStateEnvelope;
  pending: boolean;
  onSave: (envelope: CharacterStateEnvelope) => void;
}

/** The owner's envelope: which values each dimension may take, and the defaults a reset restores. */
export const CharacterStateBounds: React.FC<CharacterStateBoundsProps> = ({ envelope, pending, onSave }) => {
  const [draft, setDraft] = useState<CharacterStateEnvelope | null>(null);
  const current = draft ?? envelope;
  const bounds = (key: CharacterStateDimension) => current[key] as DimensionBounds;
  const setBounds = (key: CharacterStateDimension, next: DimensionBounds) =>
    setDraft({ ...current, [key]: next } as CharacterStateEnvelope);

  const toggle = (key: CharacterStateDimension, value: string, on: boolean) => {
    const { allowed, default: fallback } = bounds(key);
    const nextAllowed = OPTIONS[key].filter((option) => (option === value ? on : allowed.includes(option)));
    if (nextAllowed.length === 0) return;
    setBounds(key, { allowed: nextAllowed, default: nextAllowed.includes(fallback) ? fallback : nextAllowed[0] });
  };

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    onSave(current);
    setDraft(null);
  };

  return (
    <Card>
      <CardHeader>
        <h3 className="font-bold flex items-center gap-2">
          <SlidersHorizontal size={16} className="text-primary" />
          Bounds and defaults
        </h3>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Janus's state always stays inside these bounds; proposals outside them are refused. Narrowing them moves the
          current state back to the defaults where needed.
        </p>
      </CardHeader>
      <CardContent>
        <form onSubmit={save} className="space-y-4" aria-label="Bounds and defaults">
          {DIMENSIONS.map(({ key, label }) => (
            <fieldset key={key} className="space-y-2">
              <legend className="text-xs font-bold text-gray-400 uppercase tracking-wider ml-1">{label}</legend>
              <div className="flex flex-wrap gap-3">
                {OPTIONS[key].map((option) => (
                  <label key={option} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="checkbox"
                      aria-label={`Allow ${label.toLowerCase()} ${option}`}
                      checked={bounds(key).allowed.includes(option)}
                      onChange={(event) => toggle(key, option, event.target.checked)}
                    />
                    {option}
                  </label>
                ))}
              </div>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-gray-500 dark:text-gray-400">Default</span>
                <select
                  aria-label={`Default ${label.toLowerCase()}`}
                  value={bounds(key).default}
                  onChange={(event) => setBounds(key, { ...bounds(key), default: event.target.value })}
                  className={`${selectClass} max-w-48`}
                >
                  {bounds(key).allowed.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
            </fieldset>
          ))}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={current.focus_allowed}
              onChange={(event) => setDraft({ ...current, focus_allowed: event.target.checked })}
            />
            Allow a current focus
          </label>
          <Button type="submit" size="sm" icon={<Save size={14} />} disabled={pending}>
            Save bounds
          </Button>
        </form>
      </CardContent>
    </Card>
  );
};
