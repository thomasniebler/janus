import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { plannerSuggestionBulkApi } from '../../plannerSuggestionsApi';
import type { PlannerSuggestion } from '../../types';
import { ConfirmDialog } from '../ui/ConfirmDialog';

const plannerSuggestionDuplicatesQueryKey = ['planner', 'suggestions', 'duplicates'] as const;

interface PlannerSuggestionBulkActionsProps {
  /** The pending suggestions currently listed. */
  suggestions: PlannerSuggestion[] | undefined;
  /** Ticked suggestion ids; ids no longer listed (dismissed meanwhile) are ignored. */
  selectedIds: number[];
  onSelectionChange: (ids: number[]) => void;
  /** Called after any bulk dismissal, so the caller can refresh its list and drop stale drafts. */
  onDismissed: (dismissedIds: number[]) => Promise<void> | void;
}

const buttonClass =
  'rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200';

/**
 * Bulk review of planner suggestions: select all, dismiss the selection,
 * collapse duplicates to their newest copy, or clear the whole list.
 * Dismissing never changes the planner; Janus can restore dismissed suggestions in chat.
 */
export const PlannerSuggestionBulkActions: React.FC<PlannerSuggestionBulkActionsProps> = ({
  suggestions,
  selectedIds: tickedIds,
  onSelectionChange,
  onDismissed,
}) => {
  const queryClient = useQueryClient();
  const pendingIds = (suggestions ?? []).map((suggestion) => suggestion.id);
  const selectedIds = tickedIds.filter((id) => pendingIds.includes(id));
  const [confirmAll, setConfirmAll] = useState(false);
  const duplicatesQuery = useQuery({
    queryKey: [...plannerSuggestionDuplicatesQueryKey, pendingIds.length],
    queryFn: async () => (await plannerSuggestionBulkApi.duplicates()).data,
    enabled: pendingIds.length > 1,
  });
  const duplicates = duplicatesQuery.data?.duplicates ?? 0;

  const finish = async (dismissedIds: number[]) => {
    onSelectionChange([]);
    await Promise.all([
      onDismissed(dismissedIds),
      queryClient.invalidateQueries({ queryKey: plannerSuggestionDuplicatesQueryKey }),
    ]);
  };

  const dismissSelected = useMutation({
    mutationFn: async () => (await plannerSuggestionBulkApi.dismiss({ suggestion_ids: selectedIds })).data,
    onSuccess: (result) => finish(result.dismissed_ids),
  });
  const dismissAll = useMutation({
    mutationFn: async () => (await plannerSuggestionBulkApi.dismiss({ all_pending: true })).data,
    onSuccess: (result) => finish(result.dismissed_ids),
  });
  const dismissDuplicates = useMutation({
    mutationFn: async () => (await plannerSuggestionBulkApi.dismissDuplicates()).data,
    onSuccess: (result) => finish(result.dismissed_ids),
  });
  const busy = dismissSelected.isPending || dismissAll.isPending || dismissDuplicates.isPending;
  const allSelected = pendingIds.length > 0 && selectedIds.length === pendingIds.length;

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2" data-testid="planner-suggestion-bulk-actions">
      <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
        <input
          type="checkbox"
          aria-label="Select all suggestions"
          checked={allSelected}
          onChange={(event) => onSelectionChange(event.target.checked ? pendingIds : [])}
        />
        Select all
      </label>
      <button
        type="button"
        className={buttonClass}
        disabled={busy || selectedIds.length === 0}
        onClick={() => dismissSelected.mutate()}
      >
        Dismiss selected ({selectedIds.length})
      </button>
      <button
        type="button"
        className={buttonClass}
        disabled={busy || duplicates === 0}
        onClick={() => dismissDuplicates.mutate()}
        title="Keep the newest suggestion of each duplicate group and dismiss the others"
      >
        Dismiss duplicates ({duplicates})
      </button>
      <button type="button" className={buttonClass} disabled={busy} onClick={() => setConfirmAll(true)}>
        Dismiss all
      </button>
      <ConfirmDialog
        isOpen={confirmAll}
        title="Dismiss all planner suggestions?"
        message={`All ${pendingIds.length} pending suggestions leave the review list. Your planner is not changed, and Janus can restore them if you ask.`}
        confirmLabel="Dismiss all"
        tone="warning"
        onCancel={() => setConfirmAll(false)}
        onConfirm={() => {
          setConfirmAll(false);
          dismissAll.mutate();
        }}
      />
    </div>
  );
};
