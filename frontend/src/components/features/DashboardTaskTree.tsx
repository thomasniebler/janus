import { formatServerDate } from '../../lib/serverTime';
import { Trash2 } from 'lucide-react';
import { Badge } from '../ui/Badge';
import { countOpenDescendants } from '../../lib/taskTree';
import type { Task } from '../../types';

const TASK_STATUSES = ['Todo', 'In Progress', 'Done'] as const;

interface DashboardTaskTreeProps {
  /** Root tasks from `/api/dashboard/tasks`; subtasks are nested under `children`. */
  tasks: Task[];
  onStatusChange: (task: Task, status: string) => void;
  /** Asks to delete a local task (JAN-221); imported tasks are read-only and never offer it. */
  onDelete?: (task: Task) => void;
}

interface TaskNodeProps {
  task: Task;
  onStatusChange: DashboardTaskTreeProps['onStatusChange'];
  onDelete?: DashboardTaskTreeProps['onDelete'];
}

const taskKey = (task: Task) => `${task.source}-${task.id}`;

function DeleteTaskButton({ task, onDelete }: Pick<TaskNodeProps, 'task' | 'onDelete'>) {
  if (task.read_only || !onDelete) return null;
  return (
    <button
      type="button"
      aria-label={`Delete ${task.title}`}
      title="Delete task"
      onClick={() => onDelete(task)}
      className="ml-auto shrink-0 rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-accent dark:hover:bg-gray-800"
    >
      <Trash2 size={14} />
    </button>
  );
}

/**
 * Imported deadlines are calendar dates: Google Tasks keeps only the date and sends it as
 * midnight UTC, so shown in local time it would fall on the previous day west of UTC (JAN-394).
 */
const IMPORTED_DUE_FORMAT: Intl.DateTimeFormatOptions = { timeZone: 'UTC' };

function TaskMeta({ task }: { task: Task }) {
  return (
    <>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <Badge variant={task.read_only ? 'secondary' : 'primary'}>{task.source_label}</Badge>
        {task.description ? <span className="text-xs text-gray-500">{task.description}</span> : null}
      </div>
      {task.due_at && (
        <span className="text-[10px] text-accent font-bold uppercase mt-1 block">
          Due {formatServerDate(task.due_at, task.source === 'imported' ? IMPORTED_DUE_FORMAT : undefined) ?? task.due_at}
        </span>
      )}
    </>
  );
}

/**
 * A parent task with open subtasks (at any depth) acts as their category: it is
 * rendered as a group heading (title + recursive open-subtask count) with its
 * subtasks nested beneath, not as one more actionable row with its own status control.
 */
function TaskGroup({ task, onStatusChange, onDelete, openCount }: TaskNodeProps & { openCount: number }) {
  const children = task.children ?? [];
  return (
    <li data-testid={`dashboard-task-group-${taskKey(task)}`}>
      <div className="flex items-center justify-between gap-2 px-2 pt-2">
        <p className="min-w-0 truncate text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-300">
          {task.title}
        </p>
        <span className="flex shrink-0 items-center gap-1 text-[10px] font-medium text-gray-500 dark:text-gray-400">
          {openCount} open
          <DeleteTaskButton task={task} onDelete={onDelete} />
        </span>
      </div>
      <ul
        aria-label={`Subtasks of ${task.title}`}
        className="ml-3 mt-1 space-y-1 border-l border-gray-200 pl-2 dark:border-gray-700"
      >
        {children.map((child) => (
          <TaskNode key={taskKey(child)} task={child} onStatusChange={onStatusChange} onDelete={onDelete} />
        ))}
      </ul>
    </li>
  );
}

function TaskLeaf({ task, onStatusChange, onDelete }: TaskNodeProps) {
  return (
    <li
      data-testid={`dashboard-task-${taskKey(task)}`}
      className="flex items-start gap-3 rounded-lg p-2 transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
    >
      {task.read_only ? (
        <div className="mt-1 h-4 w-4 rounded border border-dashed border-gray-300 dark:border-gray-600"></div>
      ) : (
        <select
          aria-label={`Status of ${task.title}`}
          value={task.status}
          onChange={(event) => onStatusChange(task, event.target.value)}
          className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-900"
        >
          {TASK_STATUSES.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      )}
      <div className="min-w-0">
        <p className="text-sm font-medium leading-tight">{task.title}</p>
        <TaskMeta task={task} />
      </div>
      <DeleteTaskButton task={task} onDelete={onDelete} />
    </li>
  );
}

/**
 * A task is a category heading exactly when its visible subtree holds an open task; the
 * heading's "N open" is that same recursive count, so the two can never disagree.
 */
function TaskNode({ task, onStatusChange, onDelete }: TaskNodeProps) {
  const openCount = countOpenDescendants(task);
  return openCount > 0 ? (
    <TaskGroup task={task} onStatusChange={onStatusChange} onDelete={onDelete} openCount={openCount} />
  ) : (
    <TaskLeaf task={task} onStatusChange={onStatusChange} onDelete={onDelete} />
  );
}

/** The dashboard "Active Tasks" list: the local parent/subtask tree plus flat imported tasks (JAN-317). */
export function DashboardTaskTree({ tasks, onStatusChange, onDelete }: DashboardTaskTreeProps) {
  return (
    <ul aria-label="Active tasks" className="space-y-1">
      {tasks.map((task) => (
        <TaskNode key={taskKey(task)} task={task} onStatusChange={onStatusChange} onDelete={onDelete} />
      ))}
    </ul>
  );
}
