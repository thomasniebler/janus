import { formatServerDateTime } from '../../../lib/serverTime';
import React, { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight, GitBranch, RefreshCw } from 'lucide-react';

import { usePageContextSection } from '../../../hooks/usePageContext';
import { pageItem } from '../../../lib/pageContext';
import {
  RUNTIME_TASK_SOURCES,
  TASK_SOURCE_LABELS,
  isActiveTask,
  runtimeTaskApi,
  stepProgress,
  taskDetailPath,
  taskListQueryKey,
  taskRef,
  type RuntimeTaskQuery,
  type RuntimeTaskRef,
  type RuntimeTaskSource,
  type RuntimeTaskSummary,
} from '../../../lib/runtimeTasks';
import type { RuntimeTriggerStatus } from '../../../types';
import { Button } from '../../ui/Button';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { RUNTIME_TRIGGER_STATUSES, STATUS_PRESENTATION } from '../triggers/triggerPresentation';
import { TaskStatusBadge } from './TaskStatusBadge';

export const TASKS_PAGE_SIZE = 20;

const selectClassName =
  'block w-full px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-gray-700 rounded-xl outline-none text-sm uiv2:rounded-none uiv3:rounded-none';
const labelClassName = 'text-xs font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider ml-1';

const formatTime = (value: string): string => formatServerDateTime(value) ?? value;

const TaskRow: React.FC<{ task: RuntimeTaskSummary; selected: boolean }> = ({ task, selected }) => {
  const progress = stepProgress(task);
  return (
    <li>
      <Link
        to={taskDetailPath(taskRef(task))}
        aria-current={selected ? 'true' : undefined}
        data-testid="task-row"
        className={`block rounded-xl border px-3 py-2.5 transition-colors uiv2:rounded-none uiv3:rounded-none ${
          selected ? 'border-primary bg-primary/10' : 'border-gray-200 hover:border-primary/60 dark:border-gray-700'
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="min-w-0 break-words font-medium">{task.title}</span>
          <TaskStatusBadge status={task.status} />
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
          <span>{TASK_SOURCE_LABELS[task.source]}</span>
          <span>{formatTime(task.created_at)}</span>
          {progress && <span>{progress}</span>}
          {task.failed_step_count > 0 && <span className="text-red-500">{task.failed_step_count} failed</span>}
          {task.child_count > 0 && (
            <span className="inline-flex items-center gap-1">
              <GitBranch size={12} aria-hidden /> {task.child_count} delegated
            </span>
          )}
        </div>
        {task.current_step && <p className="mt-1 truncate text-xs text-primary">Now: {task.current_step}</p>}
      </Link>
    </li>
  );
};

interface TaskFiltersProps {
  source: RuntimeTaskSource | '';
  status: RuntimeTriggerStatus | '';
  onSource: (value: RuntimeTaskSource | '') => void;
  onStatus: (value: RuntimeTriggerStatus | '') => void;
}

const TaskFilters: React.FC<TaskFiltersProps> = ({ source, status, onSource, onStatus }) => (
  <div className="grid gap-3 sm:grid-cols-2">
    <label className="space-y-1">
      <span className={labelClassName}>Source</span>
      <select
        aria-label="Filter tasks by source"
        className={selectClassName}
        value={source}
        onChange={(event) => onSource(event.target.value as RuntimeTaskSource | '')}
      >
        <option value="">All sources</option>
        {RUNTIME_TASK_SOURCES.map((value) => (
          <option key={value} value={value}>{TASK_SOURCE_LABELS[value]}</option>
        ))}
      </select>
    </label>
    <label className="space-y-1">
      <span className={labelClassName}>Status</span>
      <select
        aria-label="Filter tasks by status"
        className={selectClassName}
        value={status}
        onChange={(event) => onStatus(event.target.value as RuntimeTriggerStatus | '')}
      >
        <option value="">All statuses</option>
        {RUNTIME_TRIGGER_STATUSES.map((value) => (
          <option key={value} value={value}>{STATUS_PRESENTATION[value].label}</option>
        ))}
      </select>
    </label>
  </div>
);

interface PaginationProps {
  page: number;
  shown: number;
  total: number;
  hasMore: boolean;
  onPage: (page: number) => void;
}

const Pagination: React.FC<PaginationProps> = ({ page, shown, total, hasMore, onPage }) => (
  <div className="flex items-center justify-between gap-3 text-xs text-gray-500 dark:text-gray-400" data-testid="task-pagination">
    <span>
      {page * TASKS_PAGE_SIZE + 1}–{page * TASKS_PAGE_SIZE + shown} of {total}
    </span>
    <div className="flex gap-2">
      <Button size="sm" variant="outline" icon={<ChevronLeft size={14} />} disabled={page === 0} onClick={() => onPage(Math.max(0, page - 1))}>
        Newer
      </Button>
      <Button size="sm" variant="outline" icon={<ChevronRight size={14} />} disabled={!hasMore} onClick={() => onPage(page + 1)}>
        Older
      </Button>
    </div>
  </div>
);

const EmptyNote: React.FC<{ filtered: boolean }> = ({ filtered }) => (
  <p className="text-sm text-gray-500 dark:text-gray-400">
    {filtered
      ? 'No tasks match these filters.'
      : 'No trigger runs or delegated tasks yet. They appear here as soon as an automation runs or Janus delegates work.'}
  </p>
);

const isSelected = (selected: RuntimeTaskRef | null, task: RuntimeTaskSummary): boolean =>
  selected !== null && selected.kind === taskRef(task).kind && selected.ref_id === task.ref_id;

interface TaskListBodyProps {
  tasks: RuntimeTaskSummary[];
  selected: RuntimeTaskRef | null;
  isLoading: boolean;
  isError: boolean;
  filtered: boolean;
}

const TaskListBody: React.FC<TaskListBodyProps> = ({ tasks, selected, isLoading, isError, filtered }) => {
  if (isLoading) return <RefreshCw className="animate-spin text-primary" aria-label="Loading tasks" />;
  if (isError) return <p role="alert" className="text-sm text-red-500">Could not load your tasks. Try again in a moment.</p>;
  if (tasks.length === 0) return <EmptyNote filtered={filtered} />;
  return (
    <ul aria-label="Tasks" className="space-y-2">
      {tasks.map((task) => (
        <TaskRow key={`${task.source}:${task.ref_id}`} task={task} selected={isSelected(selected, task)} />
      ))}
    </ul>
  );
};

export interface TaskListProps {
  /** The task open in the detail pane, highlighted in the list. */
  selected: RuntimeTaskRef | null;
}

/** One page of tasks; only the newest page polls (offset pages shift as tasks arrive), and only while something is in motion. */
const useTaskPage = (source: RuntimeTaskSource | '', status: RuntimeTriggerStatus | '', page: number) => {
  const query: RuntimeTaskQuery = {
    source: source || undefined,
    status: status || undefined,
    limit: TASKS_PAGE_SIZE,
    offset: page * TASKS_PAGE_SIZE,
  };
  return useQuery({
    queryKey: taskListQueryKey(query),
    queryFn: async () => (await runtimeTaskApi.list(query)).data,
    placeholderData: keepPreviousData,
    refetchInterval: (current) => (page === 0 && (current.state.data?.items ?? []).some(isActiveTask) ? 5000 : false),
  });
};

/** Filterable, paginated list of the user's trigger runs and delegated tasks (JAN-204). */
export const TaskList: React.FC<TaskListProps> = ({ selected }) => {
  const [source, setSource] = useState<RuntimeTaskSource | ''>('');
  const [status, setStatus] = useState<RuntimeTriggerStatus | ''>('');
  const [page, setPage] = useState(0);
  const { data, isLoading, isFetching, isError } = useTaskPage(source, status, page);
  const tasks = data?.items ?? [];
  usePageContextSection({
    filters: { source: source || 'all', status: status || 'all' },
    visible_items: tasks.map((task) => pageItem('runtime_task', task.ref_id, task.title)),
  });

  return (
    <Card data-ui-section="runtime_tasks">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <h2 className="font-bold">Runs and delegated tasks</h2>
          {isFetching && !isLoading && <RefreshCw size={14} className="animate-spin text-primary" aria-label="Refreshing" />}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <TaskFilters
          source={source}
          status={status}
          onSource={(value) => {
            setSource(value);
            setPage(0);
          }}
          onStatus={(value) => {
            setStatus(value);
            setPage(0);
          }}
        />
        <TaskListBody
          tasks={tasks}
          selected={selected}
          isLoading={isLoading}
          isError={isError}
          filtered={Boolean(source || status)}
        />
        {data && data.total > 0 && (
          <Pagination page={page} shown={tasks.length} total={data.total} hasMore={data.has_more} onPage={setPage} />
        )}
      </CardContent>
    </Card>
  );
};
