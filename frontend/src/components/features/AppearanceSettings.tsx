import React, { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Info, Palette } from 'lucide-react';

import { authApi } from '../../api';
import { UI_VERSIONS, useUIVersion, type UIVersion } from '../../contexts/uiVersion';
import { loadAppShell } from '../appShells';
import { Card } from '../ui/Card';
import type { User } from '../../types';

interface ShellOption {
  id: UIVersion;
  name: string;
  tagline: string;
  description: string;
}

const SHELL_OPTIONS: ShellOption[] = [
  {
    id: 'v1',
    name: 'Classic (UIv1)',
    tagline: 'Dark wallpaper, glass panels',
    description:
      'The original Janus shell: a blurred wallpaper backdrop, translucent glass header and panels, and a left sidebar for navigation.',
  },
  {
    id: 'v2',
    name: 'Terminal (UIv2)',
    tagline: 'Near-black HUD, phosphor lime',
    description:
      'A high-contrast console shell: a near-black CRT backdrop with a faint grid and scanlines, hard-edged panels, monospaced uppercase chrome, acid-lime phosphor accents, and a command bar on top with a toggleable chat drawer.',
  },
  {
    id: 'v3',
    name: 'Rusty Console (UIv3)',
    tagline: 'Black-red decay, typewriter type',
    description:
      'A weathered hacker terminal: a warm almost-black-red ground stained with corrosion and film grain, rust-red rules around every panel and nav key, typewriter lettering, and a slow roll band drifting down a tired tube.',
  },
];

/**
 * Appearance settings: switches the app shell between UIv1, UIv2 and UIv3. The switch is
 * applied optimistically (instant re-render) and persisted to the backend in the
 * background; localStorage is written through so reloads never flash the wrong shell.
 */
export const AppearanceSettings: React.FC = () => {
  const { uiVersion, preferredUIVersion, isOverridden, setUIVersion } = useUIVersion();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  // Shells are lazy-loaded chunks (JAN-179). Fetch the alternatives while the picker is on
  // screen so a switch renders the new shell in the same commit as its <html> class, with
  // no loading flash. A failed prefetch is harmless: the shell host retries and reports it.
  useEffect(() => {
    for (const version of UI_VERSIONS) {
      loadAppShell(version).catch(() => undefined);
    }
  }, []);

  const mutation = useMutation({
    mutationFn: (version: UIVersion) => authApi.updateUIPreference(version),
    onSuccess: (response) => {
      setError(null);
      queryClient.setQueryData<User>(['me'], (previous) =>
        previous ? { ...previous, ...response.data } : response.data
      );
    },
    onError: (_error, _version, context) => {
      setError('Could not save your appearance preference. It will reset on the next reload.');
      const previous = context as UIVersion | undefined;
      if (previous) {
        setUIVersion(previous);
      }
    },
    onMutate: (version: UIVersion) => {
      const previous = preferredUIVersion;
      setError(null);
      setUIVersion(version);
      return previous;
    },
  });

  return (
    <Card className="p-6">
      <div className="mb-1 flex items-center gap-2">
        <Palette size={18} className="text-primary uiv2:text-v2-accent uiv3:text-v3-accent-bright" />
        <h2 className="text-lg font-semibold">Appearance</h2>
      </div>
      <p className="mb-5 text-sm text-gray-500 dark:text-gray-400">
        Choose which app shell Janus renders. Every option shows exactly the same pages and data —
        only the chrome around them changes, and you can switch back at any time.
      </p>

      <div role="radiogroup" aria-label="App shell" className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {SHELL_OPTIONS.map((option) => {
          const isSelected = preferredUIVersion === option.id;
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={isSelected}
              onClick={() => mutation.mutate(option.id)}
              disabled={isSelected}
              className={`flex flex-col items-start gap-1.5 rounded-xl border p-4 text-left transition-colors ${
                isSelected
                  ? 'border-primary bg-primary/5 uiv2:border-v2-accent uiv2:bg-v2-accent-soft uiv2:shadow-[0_0_20px_-4px_rgba(198,242,74,0.35)] uiv3:border-2 uiv3:border-v3-accent uiv3:bg-v3-accent-soft'
                  : 'border-gray-200 hover:border-primary/50 dark:border-gray-700 uiv2:border-v2-border uiv2:bg-v2-surface uiv2:hover:border-v2-accent uiv3:border-2 uiv3:border-v3-border uiv3:bg-v3-surface uiv3:hover:border-v3-accent'
              }`}
            >
              <span className="flex w-full items-center justify-between gap-2">
                <span className="text-sm font-bold">{option.name}</span>
                {isSelected && (
                  <span className="inline-flex items-center gap-1 text-[11px] font-bold uppercase tracking-wider text-primary uiv2:text-v2-accent uiv3:text-v3-accent-bright">
                    <Check size={13} />
                    Active
                  </span>
                )}
              </span>
              <span className="text-[11px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-500 uiv2:font-terminal uiv2:tracking-[0.18em] uiv2:text-v2-accent-dim uiv3:font-typewriter uiv3:tracking-[0.16em] uiv3:text-v3-ember">
                {option.tagline}
              </span>
              <span className="text-sm text-gray-600 dark:text-gray-400">{option.description}</span>
            </button>
          );
        })}
      </div>

      {error && <p className="mt-4 text-sm font-medium text-red-600 dark:text-red-400">{error}</p>}

      {isOverridden && (
        <p className="mt-4 flex items-start gap-2 text-sm text-gray-500 dark:text-gray-400">
          <Info size={15} className="mt-0.5 shrink-0" />
          <span>
            A <code>?ui={uiVersion}</code> override is pinning the shell for this page load. Your saved
            preference still applies once you drop the query parameter.
          </span>
        </p>
      )}

      <p className="mt-4 flex items-start gap-2 text-sm text-gray-500 dark:text-gray-400">
        <Info size={15} className="mt-0.5 shrink-0" />
        <span>
          Having trouble? Add <code>?ui=v1</code> to the URL to force the classic UI.
        </span>
      </p>
    </Card>
  );
};
