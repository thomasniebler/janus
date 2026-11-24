import React from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { TaskDetail } from '../components/features/tasks/TaskDetail';
import { TaskList } from '../components/features/tasks/TaskList';
import { Card, CardContent } from '../components/ui/Card';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { usePageContext } from '../hooks/usePageContext';
import { pageItem } from '../lib/pageContext';
import { readTaskRef } from '../lib/runtimeTasks';

/**
 * The Tasks page (JAN-204): what the plan-driven runtime did outside chat --
 * trigger runs, automation (definition) runs and delegated child tasks -- with
 * each task's persisted plan steps and tool trace. Live chat turns stay on the
 * Chat page (turn timeline, JAN-316); definitions stay on Settings -> Triggers.
 * The open task lives in the URL (`?kind=trigger&task=<id>`) so it can be linked.
 */
const TasksPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const selected = readTaskRef(searchParams);
  usePageContext({
    page: 'Agent tasks',
    title: 'Agent Tasks',
    selection: selected ? pageItem('runtime_task', selected.ref_id, `${selected.kind} task`) : null,
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Agent Tasks</h1>
        <p className="mt-1 text-gray-500 dark:text-gray-400">
          Everything Janus ran without you watching: trigger runs, your automations, and tasks it delegated, with each
          plan step and the tools it used. Live chat turns show their steps in <Link to="/chat" className="text-primary hover:underline">Chat</Link>;
          automations are managed in <Link to="/settings?tab=runtime" className="text-primary hover:underline">Settings → Triggers</Link>.
        </p>
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <ErrorBoundary title="The task list crashed">
          <TaskList selected={selected} />
        </ErrorBoundary>
        <ErrorBoundary title="The task detail crashed">
          {selected ? (
            <TaskDetail key={`${selected.kind}:${selected.ref_id}`} taskRef={selected} />
          ) : (
            <Card>
              <CardContent>
                <p className="text-sm text-gray-500 dark:text-gray-400">Select a task to see its plan steps and tool trace.</p>
              </CardContent>
            </Card>
          )}
        </ErrorBoundary>
      </div>
    </div>
  );
};

export default TasksPage;
