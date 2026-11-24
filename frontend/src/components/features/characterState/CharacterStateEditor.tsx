import React, { useState } from 'react';
import { RotateCcw, Save } from 'lucide-react';
import type { CharacterStateValues, CharacterStateView } from '../../../lib/characterState';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { DIMENSIONS, selectClass } from './characterStateShared';

interface CharacterStateEditorProps {
  view: CharacterStateView;
  pending: boolean;
  onSave: (values: CharacterStateValues) => void;
  onReset: () => void;
}

/** The owner's direct edit of the current state, limited to the values the envelope allows. */
export const CharacterStateEditor: React.FC<CharacterStateEditorProps> = ({ view, pending, onSave, onReset }) => {
  const [draft, setDraft] = useState<CharacterStateValues | null>(null);
  const current = draft ?? view.state;
  const update = (patch: Partial<CharacterStateValues>) => setDraft({ ...current, ...patch });

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    onSave({ ...current, current_focus: current.current_focus?.trim() || null });
    setDraft(null);
  };

  return (
    <Card>
      <CardHeader>
        <h3 className="font-bold">Current adaptive state</h3>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Revision {view.revision}. Janus can only propose changes; yours apply directly. None of this changes the
          constitution, tools, or permissions.
        </p>
      </CardHeader>
      <CardContent>
        <form onSubmit={save} className="space-y-4" aria-label="Edit adaptive state">
          <div className="grid gap-4 md:grid-cols-3">
            {DIMENSIONS.map(({ key, label, hint }) => (
              <div key={key} className="space-y-1.5">
                <label className="text-xs font-bold text-gray-400 uppercase tracking-wider ml-1" htmlFor={`character-state-${key}`}>
                  {label}
                </label>
                <select
                  id={`character-state-${key}`}
                  value={current[key]}
                  onChange={(event) => update({ [key]: event.target.value } as Partial<CharacterStateValues>)}
                  className={selectClass}
                >
                  {(view.envelope[key].allowed as string[]).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 dark:text-gray-400 ml-1">{hint}</p>
              </div>
            ))}
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-bold text-gray-400 uppercase tracking-wider ml-1" htmlFor="character-state-focus">
              Current focus
            </label>
            <input
              id="character-state-focus"
              type="text"
              value={current.current_focus ?? ''}
              maxLength={view.focus_max_chars}
              disabled={!view.envelope.focus_allowed}
              onChange={(event) => update({ current_focus: event.target.value })}
              placeholder={view.envelope.focus_allowed ? 'e.g. Quarterly report' : 'Switched off in the bounds below'}
              className={selectClass}
            />
            <p className="text-xs text-gray-500 dark:text-gray-400 ml-1">
              One line, at most {view.focus_max_chars} characters. Janus treats it as data, never as instructions.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" icon={<Save size={14} />} disabled={pending}>
              Save state
            </Button>
            <Button type="button" size="sm" variant="outline" icon={<RotateCcw size={14} />} disabled={pending} onClick={onReset}>
              Reset to defaults
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
};
