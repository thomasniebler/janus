import { formatServerDateTime } from '../../../lib/serverTime';
import React from 'react';
import { History, Undo2 } from 'lucide-react';
import type { CharacterStateChange } from '../../../lib/characterState';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { describeValues } from './characterStateShared';

const ACTION_LABELS: Record<CharacterStateChange['action'], string> = {
  edit: 'You edited the state',
  proposal: 'Janus proposal, approved by you',
  rollback: 'You restored an earlier state',
  reset: 'You reset to the defaults',
  envelope: 'Your new bounds pulled the state back inside them',
};

interface CharacterStateHistoryProps {
  changes: CharacterStateChange[];
  currentRevision: number;
  busyId: number | null;
  onRestore: (changeId: number) => void;
  /** More (older) changes exist beyond the loaded pages. */
  hasMore?: boolean;
  loadingMore?: boolean;
  loadError?: boolean;
  onLoadMore?: () => void;
}

/** The audit trail, newest first; any earlier state can be restored. */
export const CharacterStateHistory: React.FC<CharacterStateHistoryProps> = ({
  changes,
  currentRevision,
  busyId,
  onRestore,
  hasMore = false,
  loadingMore = false,
  loadError = false,
  onLoadMore,
}) => (
  <Card>
    <CardHeader>
      <h3 className="font-bold flex items-center gap-2">
        <History size={16} className="text-primary" />
        History
      </h3>
      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Every change, who made it, and what it was before.</p>
    </CardHeader>
    <CardContent>
      {loadError && <p role="alert" className="mb-2 text-sm text-red-400">Could not load the history.</p>}
      {changes.length === 0 ? (
        !loadError && <p className="text-sm text-gray-500 dark:text-gray-400">No changes yet: Janus uses your defaults.</p>
      ) : (
        <ol className="space-y-2">
          {changes.map((change) => (
            <li
              key={change.id}
              aria-label={`Revision ${change.revision}`}
              className="rounded-xl border border-gray-200 dark:border-gray-700 p-3 flex flex-wrap items-start justify-between gap-3"
            >
              <div className="space-y-1 text-sm min-w-0">
                <p className="font-medium">
                  Revision {change.revision}: {ACTION_LABELS[change.action]}
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400 break-words" data-testid="character-change-new">
                  {describeValues(change.new_state)}
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400 break-words">
                  Before: {describeValues(change.previous_state)} · {formatServerDateTime(change.created_at) ?? change.created_at}
                </p>
              </div>
              {change.revision !== currentRevision && (
                <Button
                  size="sm"
                  variant="outline"
                  icon={<Undo2 size={14} />}
                  isLoading={busyId === change.id}
                  onClick={() => onRestore(change.id)}
                >
                  Restore
                </Button>
              )}
            </li>
          ))}
        </ol>
      )}
      {hasMore && onLoadMore && (
        <Button size="sm" variant="outline" className="mt-3" isLoading={loadingMore} onClick={onLoadMore}>
          Load older changes
        </Button>
      )}
    </CardContent>
  </Card>
);
