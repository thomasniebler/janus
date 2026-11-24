import { formatServerDateTime } from '../../../lib/serverTime';
import React from 'react';
import axios from 'axios';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowUpRight, GitBranch, MessageSquare, RefreshCw, Settings2 } from 'lucide-react';

import {
  TASK_SOURCE_LABELS,
  isActiveTask,
  runtimeTaskApi,
  taskDetailPath,
  taskDetailQueryKey,
  taskRef,
  type RuntimeTaskDetail,
  type RuntimeTaskRef,
} from '../../../lib/runtimeTasks';
import { Card, CardContent } from '../../ui/Card';
import { TaskStatusBadge } from './TaskStatusBadge';
import { TaskStepList } from './TaskStepList';

const linkClassName = 'inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline';
const sectionTitleClassName = 'text-xs font-bold uppercase tracking-wider text-gray-400 dark:text-gray-500';

const formatTime = (value: string | null): string => (value ? (formatServerDateTime(value) ?? value) : '—');

/** Where this task connects to the rest of Janus: its definition, chat, the run that spawned it. */
const TaskLinks: React.FC<{ task: RuntimeTaskDetail }> = ({ task }) => (
  <div className="flex flex-wrap gap-x-4 gap-y-2">
    {task.definition_id != null && (
      <Link to="/settings?tab=runtime" className={linkClassName}>
        <Settings2 size={14} aria-hidden /> Open definition “{task.definition_name ?? 'automation'}” in Settings → Triggers
      </Link>
    )}
    {task.conversation_id != null && (
      <Link to={`/chat?conversation=${task.conversation_id}`} className={linkClassName}>
        <MessageSquare size={14} aria-hidden /> Open the chat that delegated it
      </Link>
    )}
    {task.parent && (
      <Link to={taskDetailPath(task.parent)} className={linkClassName}>
        <ArrowUpRight size={14} aria-hidden /> {task.source === 'delegated' ? 'Open the run that delegated it' : 'Open the run that scheduled it'}
      </Link>
    )}
  </div>
);

const Facts: React.FC<{ task: RuntimeTaskDetail }> = ({ task }) => {
  const facts: Array<[string, string]> = [
    ['Source', TASK_SOURCE_LABELS[task.source]],
    ['Created', formatTime(task.created_at)],
    ['Finished', formatTime(task.completed_at)],
    ['Plan', task.plan_status ?? 'not started'],
  ];
  if (task.trigger_source) facts.push(['Trigger source', task.trigger_source]);
  if (task.attempt_count != null) facts.push(['Attempts', String(task.attempt_count)]);
  if (task.task_mode) facts.push(['Task mode', task.task_mode]);
  if (task.allowed_tool_ids.length > 0) facts.push(['Granted tools', task.allowed_tool_ids.join(', ')]);
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
      {facts.map(([label, value]) => (
        <div key={label} className="flex gap-2">
          <dt className="text-gray-500 dark:text-gray-400">{label}:</dt>
          <dd className="min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
};

const Children: React.FC<{ task: RuntimeTaskDetail }> = ({ task }) =>
  task.children.length === 0 ? null : (
    <section className="space-y-2">
      <h3 className={sectionTitleClassName}>Delegated tasks</h3>
      <ul aria-label="Delegated tasks" className="space-y-1">
        {task.children.map((child) => (
          <li key={child.ref_id} className="flex items-center justify-between gap-2">
            <Link to={taskDetailPath(taskRef(child))} className={linkClassName}>
              <GitBranch size={14} aria-hidden /> {child.title}
            </Link>
            <TaskStatusBadge status={child.status} />
          </li>
        ))}
      </ul>
    </section>
  );

/** Errors recorded outside the steps: the trigger's last error, the plan's reason, the delegation's outcome. */
const Outcome: React.FC<{ task: RuntimeTaskDetail }> = ({ task }) => (
  <>
    {task.last_error && <p role="alert" className="break-words text-sm text-red-500">Last error: {task.last_error}</p>}
    {task.delegation_error && <p role="alert" className="break-words text-sm text-red-500">Error: {task.delegation_error}</p>}
    {task.reason && <p className="break-words text-sm text-gray-500 dark:text-gray-400">Plan note: {task.reason}</p>}
    {task.delegation_result && (
      <section className="space-y-1">
        <h3 className={sectionTitleClassName}>Result handed back</h3>
        <p className="whitespace-pre-wrap break-words text-sm text-gray-700 dark:text-gray-300">{task.delegation_result}</p>
      </section>
    )}
  </>
);

/** The persisted detail of one task: status, plan steps, tool trace, and links (JAN-204). */
export const TaskDetailView: React.FC<{ task: RuntimeTaskDetail }> = ({ task }) => (
  <div className="space-y-5" data-testid="task-detail">
    <div className="space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 className="min-w-0 break-words text-xl font-bold">{task.title}</h2>
        <TaskStatusBadge status={task.status} />
      </div>
      <TaskLinks task={task} />
    </div>
    <Facts task={task} />
    {task.objective && (
      <section className="space-y-1">
        <h3 className={sectionTitleClassName}>Objective</h3>
        <p className="whitespace-pre-wrap break-words text-sm">{task.objective}</p>
      </section>
    )}
    <Outcome task={task} />
    <section className="space-y-2">
      <h3 className={sectionTitleClassName}>Plan steps</h3>
      <TaskStepList steps={task.steps} />
    </section>
    <Children task={task} />
  </div>
);

const isNotFound = (error: unknown): boolean => axios.isAxiosError(error) && error.response?.status === 404;

/** Loads and shows one task; polls while it is still queued or running. */
export const TaskDetail: React.FC<{ taskRef: RuntimeTaskRef }> = ({ taskRef: ref }) => {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: taskDetailQueryKey(ref),
    queryFn: async () => (await runtimeTaskApi.detail(ref)).data,
    refetchInterval: (current) => (current.state.data && isActiveTask(current.state.data) ? 5000 : false),
    retry: (count, failure) => !isNotFound(failure) && count < 2,
  });
  return (
    <Card data-ui-section="runtime_task_detail">
      <CardContent>
        {isLoading && <RefreshCw className="animate-spin text-primary" aria-label="Loading task" />}
        {isError && (
          <p role="alert" className="text-sm text-red-500">
            {isNotFound(error) ? 'This task does not exist or is not yours.' : 'Could not load this task. Try again in a moment.'}
          </p>
        )}
        {data && <TaskDetailView task={data} />}
      </CardContent>
    </Card>
  );
};
