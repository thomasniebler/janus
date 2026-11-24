import React, { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Sparkles, X } from 'lucide-react';
import { statusApi } from '../../api';
import { readLastSeenVersion, writeLastSeenVersion } from '../../lib/lastSeenVersion';
import { ChangelogLink } from './ChangelogLink';

const MAX_HIGHLIGHTS = 5;
const AUTO_HIDE_MS = 20000;

/** Turns release-please's `**scope:** text` bullets into plain text. */
const plainItem = (item: string) => item.replace(/\*\*([^*]+)\*\*/g, '$1');

/**
 * A short flash after Janus was updated: what the releases since this browser's last visit
 * changed, with a link to the full changelog. A first visit only records the version.
 */
export const WhatsNewFlash: React.FC<{ userId: number }> = ({ userId }) => {
  // Read once: the stored version moves to the current one when the notes are dismissed.
  const [since] = useState(() => readLastSeenVersion(userId));
  const [dismissed, setDismissed] = useState(false);
  const [paused, setPaused] = useState(false);

  const { data } = useQuery({
    queryKey: ['version-info', since],
    queryFn: async () => (await statusApi.getVersion(since)).data,
    staleTime: Infinity,
    retry: false,
  });

  const hasNotes = Boolean(data && data.releases.length > 0);

  // Nothing to show (a first visit, or the same version): remember the version right away.
  // With notes, the version is remembered once they were dismissed or timed out, so a reload
  // before that shows them again.
  const seen = Boolean(data) && (!hasNotes || dismissed);
  useEffect(() => {
    if (data && seen) writeLastSeenVersion(userId, data.version);
  }, [data, seen, userId]);

  useEffect(() => {
    if (!hasNotes || paused) return undefined;
    const timer = window.setTimeout(() => setDismissed(true), AUTO_HIDE_MS);
    return () => window.clearTimeout(timer);
  }, [hasNotes, paused]);

  if (!data || !hasNotes || dismissed) return null;

  const items = data.releases.flatMap((release) => release.groups.flatMap((group) => group.items));
  const shown = items.slice(0, MAX_HIGHLIGHTS);
  const hidden = items.length - shown.length;

  return (
    <div
      role="status"
      aria-label="What's new"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
      className="fixed bottom-4 right-4 z-50 w-[22rem] max-w-[calc(100vw-2rem)] rounded-2xl border border-gray-200 bg-white p-4 text-sm shadow-xl dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-center gap-2 font-bold">
          <Sparkles size={16} aria-hidden="true" />
          Janus updated to v{data.version}
        </p>
        <button
          type="button"
          aria-label="Dismiss"
          onClick={() => setDismissed(true)}
          className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
        >
          <X size={14} />
        </button>
      </div>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-gray-700 dark:text-gray-300">
        {shown.map((item, index) => (
          <li key={`${index}-${item}`}>{plainItem(item)}</li>
        ))}
      </ul>
      {hidden > 0 && <p className="mt-1 text-xs text-gray-500">and {hidden} more</p>}
      <ChangelogLink
        href={data.changelog_url}
        className="mt-3 inline-block font-semibold text-blue-600 hover:underline dark:text-blue-400"
      >
        Read the full changelog
      </ChangelogLink>
    </div>
  );
};
