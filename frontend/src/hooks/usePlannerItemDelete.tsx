import { useMutation } from '@tanstack/react-query';
import axios from 'axios';
import { plannerApi } from '../api';
import { useConfirmDialog } from './useConfirmDialog';

export interface PlannerDeleteTarget {
  kind: 'task' | 'event';
  id: number;
  title: string;
}

const deleteErrorMessage = (error: unknown) => {
  const detail = axios.isAxiosError(error) ? (error.response?.data as { detail?: unknown } | undefined)?.detail : null;
  return typeof detail === 'string' ? detail : 'Could not delete that planner item.';
};

/**
 * Deletes a local planner task or event after the user confirms (JAN-221). Render
 * `feedback` (the confirmation dialog and any delete error) once; `onDeleted` runs
 * after the backend removed the item, e.g. to refetch. A deleted task's subtasks are
 * kept and move up a level.
 */
export function usePlannerItemDelete(onDeleted: () => Promise<unknown> | void) {
  const { confirm, dialog } = useConfirmDialog();
  const deleteItem = useMutation({
    mutationFn: (target: PlannerDeleteTarget) =>
      plannerApi.deleteLocalItem(target.kind === 'task' ? 'tasks' : 'events', target.id),
    onSuccess: () => onDeleted(),
  });

  const requestDelete = (target: PlannerDeleteTarget) =>
    confirm({
      title: target.kind === 'task' ? 'Delete task?' : 'Delete event?',
      message:
        target.kind === 'task'
          ? `Delete "${target.title}"? Its subtasks stay and move up a level.`
          : `Delete "${target.title}"?`,
      confirmLabel: 'Delete',
      tone: 'warning',
      onConfirm: () => deleteItem.mutate(target),
    });

  const feedback = (
    <>
      {dialog}
      {deleteItem.error ? (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200"
        >
          {deleteErrorMessage(deleteItem.error)}
        </div>
      ) : null}
    </>
  );

  return { requestDelete, feedback };
}
