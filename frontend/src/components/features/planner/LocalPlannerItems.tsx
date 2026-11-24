import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { plannerApi } from '../../../api';
import { formatServerDateTime } from '../../../lib/serverTime';
import { usePlannerItemDelete } from '../../../hooks/usePlannerItemDelete';
import { Card, CardContent, CardHeader } from '../../ui/Card';
import { Badge } from '../../ui/Badge';
import { DashboardTaskTree } from '../DashboardTaskTree';
import { Trash2 } from 'lucide-react';
import { plannerEventsQueryKey, plannerTasksQueryKey } from './plannerQueryKeys';

const inputClass = 'rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900';
const submitClass =
  'rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900';

/** Upcoming events from every source (local, Google, Outlook) with a form for local ones. */
export const PlannerEvents: React.FC = () => {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');

  const { data: events, isLoading } = useQuery({
    queryKey: plannerEventsQueryKey,
    queryFn: async () => (await plannerApi.getEvents()).data,
  });
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: plannerEventsQueryKey }),
    queryClient.invalidateQueries({ queryKey: ['dashboard', 'schedule'] }),
  ]);

  const create = useMutation({
    mutationFn: () =>
      plannerApi.createLocalEvent({
        title,
        start_time: new Date(start).toISOString(),
        end_time: new Date(end || start).toISOString(),
      }),
    onSuccess: async () => {
      setTitle('');
      setStart('');
      setEnd('');
      await refresh();
    },
  });
  const { requestDelete, feedback } = usePlannerItemDelete(refresh);

  return (
    <Card>
      <CardHeader>
        <h2 className="text-lg font-bold">Events</h2>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="grid gap-2 rounded-xl border border-dashed border-gray-200 p-3 dark:border-gray-700"
          onSubmit={(event) => {
            event.preventDefault();
            if (title.trim() && start) create.mutate();
          }}
        >
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a local event" className={inputClass} />
          <div className="grid grid-cols-2 gap-2">
            <input type="datetime-local" aria-label="Event start" value={start} onChange={(e) => setStart(e.target.value)} className={inputClass} />
            <input type="datetime-local" aria-label="Event end" value={end} onChange={(e) => setEnd(e.target.value)} className={inputClass} />
          </div>
          <button type="submit" disabled={create.isPending || !title.trim() || !start} className={submitClass}>
            {create.isPending ? 'Saving…' : 'Add event'}
          </button>
        </form>
        {isLoading && <p className="text-sm text-gray-500">Loading events…</p>}
        {events && events.length === 0 && <p className="text-sm text-gray-500">No events yet.</p>}
        <ul className="space-y-2">
          {(events ?? []).map((event) => (
            <li key={`${event.source}-${event.id}`} className="flex items-start gap-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{event.title}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {formatServerDateTime(event.start_time)}
                  {event.location ? ` · ${event.location}` : ''}
                </p>
              </div>
              <Badge variant="secondary" className="shrink-0 text-xs">
                {event.source_label}
              </Badge>
              {!event.read_only && (
                <button
                  type="button"
                  aria-label={`Delete ${event.title}`}
                  onClick={() => requestDelete({ kind: 'event', id: event.id, title: event.title })}
                  className="shrink-0 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-accent dark:hover:bg-gray-800"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
        {feedback}
      </CardContent>
    </Card>
  );
};

/** Tasks from every source, with a form for local ones. */
export const PlannerTasks: React.FC = () => {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [dueAt, setDueAt] = useState('');

  const { data: tasks, isLoading } = useQuery({
    queryKey: plannerTasksQueryKey,
    queryFn: async () => (await plannerApi.getTasks()).data,
  });
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: plannerTasksQueryKey }),
    queryClient.invalidateQueries({ queryKey: ['dashboard', 'tasks'] }),
  ]);

  const create = useMutation({
    mutationFn: () => plannerApi.createLocalTask({ title, due_at: dueAt ? new Date(dueAt).toISOString() : null }),
    onSuccess: async () => {
      setTitle('');
      setDueAt('');
      await refresh();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => plannerApi.updateLocalTask(id, { status }),
    onSuccess: refresh,
  });
  const { requestDelete, feedback } = usePlannerItemDelete(refresh);

  return (
    <Card>
      <CardHeader>
        <h2 className="text-lg font-bold">Tasks</h2>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="grid gap-2 rounded-xl border border-dashed border-gray-200 p-3 dark:border-gray-700"
          onSubmit={(event) => {
            event.preventDefault();
            if (title.trim()) create.mutate();
          }}
        >
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a local task" className={inputClass} />
          <input type="datetime-local" aria-label="Task due" value={dueAt} onChange={(e) => setDueAt(e.target.value)} className={inputClass} />
          <button type="submit" disabled={create.isPending || !title.trim()} className={submitClass}>
            {create.isPending ? 'Saving…' : 'Add task'}
          </button>
        </form>
        {isLoading && <p className="text-sm text-gray-500">Loading tasks…</p>}
        {tasks && tasks.length === 0 && <p className="text-sm text-gray-500">No tasks yet.</p>}
        {tasks && tasks.length > 0 && (
          <DashboardTaskTree
            tasks={tasks}
            onStatusChange={(task, status) => update.mutate({ id: task.id, status })}
            onDelete={(task) => requestDelete({ kind: 'task', id: task.id, title: task.title })}
          />
        )}
        {feedback}
      </CardContent>
    </Card>
  );
};
