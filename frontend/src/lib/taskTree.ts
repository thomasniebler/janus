import type { ConfidenceLevel, PlannerItemSource } from '../types';

/**
 * A planner task as the API returns it (re-exported from `types.ts`). Local tasks carry their
 * parent/subtask hierarchy; tree responses such as `/api/dashboard/tasks` nest subtasks under
 * `children` (JAN-317). Imported (read-only) tasks are always flat roots.
 */
export interface Task {
  id: number;
  kind: 'task';
  source: PlannerItemSource;
  source_label: string;
  read_only: boolean;
  title: string;
  description?: string | null;
  status: string;
  parent_task_id?: number | null;
  sort_order?: number | null;
  child_count?: number;
  open_child_count?: number;
  children?: Task[];
  priority?: string | null;
  due_at?: string | null;
  scheduled_for?: string | null;
  completed_at?: string | null;
  confidence?: ConfidenceLevel | null;
  external_id?: string | null;
  planner_account_id?: number | null;
}

/** Depth-first flattening of a nested task tree (each parent before its subtasks). */
export const flattenTaskTree = (tasks: readonly Task[]): Task[] =>
  tasks.flatMap((task) => [task, ...flattenTaskTree(task.children ?? [])]);

/**
 * Open (not Done) tasks anywhere below `task` in the visible tree. A done subtask can stay in
 * the tree as the ancestor of an open one, so the API's direct-children `open_child_count`
 * would undercount; this walks every level instead (JAN-317).
 */
export const countOpenDescendants = (task: Task): number =>
  (task.children ?? []).reduce(
    (total, child) => total + (child.status === 'Done' ? 0 : 1) + countOpenDescendants(child),
    0
  );
