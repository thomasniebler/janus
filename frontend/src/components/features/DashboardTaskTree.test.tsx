import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { taskListSchema } from '../../lib/schemas';
import { flattenTaskTree } from '../../lib/taskTree';
import type { Task } from '../../types';
import { DashboardTaskTree } from './DashboardTaskTree';

const task = (overrides: Partial<Task> & Pick<Task, 'id' | 'title'>): Task => ({
  kind: 'task',
  source: 'manual',
  source_label: 'Manual',
  read_only: false,
  status: 'Todo',
  description: null,
  parent_task_id: null,
  child_count: 0,
  open_child_count: 0,
  children: [],
  ...overrides,
});

// The shape `/api/dashboard/tasks` returns since JAN-317: the local task tree, then imported tasks.
const treeResponse: Task[] = [
  task({
    id: 1,
    title: 'Work',
    child_count: 2,
    open_child_count: 2,
    children: [
      task({ id: 2, title: 'Write report', parent_task_id: 1 }),
      task({
        id: 3,
        title: 'Plan launch',
        parent_task_id: 1,
        child_count: 1,
        open_child_count: 1,
        children: [task({ id: 4, title: 'Book venue', parent_task_id: 3 })],
      }),
    ],
  }),
  task({ id: 5, title: 'Standalone task' }),
  task({ id: 1, title: 'JAN-9 Imported issue', source: 'imported', source_label: 'Jira', read_only: true }),
];

describe('DashboardTaskTree (JAN-317)', () => {
  it('renders a parent task as a category heading with its subtasks nested, not as one more task', () => {
    render(<DashboardTaskTree tasks={treeResponse} onStatusChange={vi.fn()} />);

    // The category has no status control of its own ...
    expect(screen.queryByRole('combobox', { name: 'Status of Work' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Status of Plan launch' })).not.toBeInTheDocument();
    // ... and its subtasks are nested beneath it, including deeper levels.
    const workSubtasks = screen.getByRole('list', { name: 'Subtasks of Work' });
    expect(within(workSubtasks).getByText('Write report')).toBeInTheDocument();
    expect(within(workSubtasks).getByText('Book venue')).toBeInTheDocument();
    const launchSubtasks = screen.getByRole('list', { name: 'Subtasks of Plan launch' });
    expect(within(launchSubtasks).getByRole('combobox', { name: 'Status of Book venue' })).toBeInTheDocument();
    // "N open" counts every open task in the subtree, not just direct children.
    expect(screen.getByTestId('dashboard-task-group-manual-1')).toHaveTextContent('3 open');

    // Leaf tasks keep their status controls; imported tasks stay read-only roots.
    expect(screen.getByRole('combobox', { name: 'Status of Standalone task' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Status of Write report' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /JAN-9 Imported issue/ })).not.toBeInTheDocument();
    expect(screen.getByTestId('dashboard-task-imported-1')).toHaveTextContent('Jira');
  });

  it('changes the status of a nested subtask', async () => {
    const onStatusChange = vi.fn();
    const user = userEvent.setup();
    render(<DashboardTaskTree tasks={treeResponse} onStatusChange={onStatusChange} />);

    await user.selectOptions(screen.getByRole('combobox', { name: 'Status of Book venue' }), 'Done');

    expect(onStatusChange).toHaveBeenCalledWith(expect.objectContaining({ id: 4 }), 'Done');
  });

  it('renders a parent whose subtasks are all done as an ordinary task', () => {
    render(
      <DashboardTaskTree
        tasks={[task({ id: 7, title: 'Errands', child_count: 0, open_child_count: 0, children: [] })]}
        onStatusChange={vi.fn()}
      />
    );

    expect(screen.getByRole('combobox', { name: 'Status of Errands' })).toBeInTheDocument();
  });

  it('counts an open grandchild under a completed child', () => {
    // list_local_task_tree keeps a done child as the ancestor of an open grandchild; the API's
    // direct-children open_child_count is then 0 for the root.
    render(
      <DashboardTaskTree
        tasks={[
          task({
            id: 10,
            title: 'Home',
            child_count: 1,
            open_child_count: 0,
            children: [
              task({
                id: 11,
                title: 'Renovate',
                status: 'Done',
                parent_task_id: 10,
                child_count: 1,
                open_child_count: 1,
                children: [task({ id: 12, title: 'Paint hallway', parent_task_id: 11 })],
              }),
            ],
          }),
        ]}
        onStatusChange={vi.fn()}
      />
    );

    expect(screen.getByTestId('dashboard-task-group-manual-10')).toHaveTextContent('1 open');
    expect(screen.getByTestId('dashboard-task-group-manual-11')).toHaveTextContent('1 open');
    expect(screen.queryByRole('combobox', { name: 'Status of Home' })).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('list', { name: 'Subtasks of Renovate' })).getByRole('combobox', {
        name: 'Status of Paint hallway',
      })
    ).toBeInTheDocument();
  });

  it('renders a parent whose nested children are all done as an ordinary task', () => {
    render(
      <DashboardTaskTree
        tasks={[
          task({
            id: 20,
            title: 'Garden',
            child_count: 1,
            open_child_count: 0,
            children: [task({ id: 21, title: 'Mow lawn', status: 'Done', parent_task_id: 20 })],
          }),
        ]}
        onStatusChange={vi.fn()}
      />
    );

    expect(screen.getByRole('combobox', { name: 'Status of Garden' })).toBeInTheDocument();
    expect(screen.queryByTestId('dashboard-task-group-manual-20')).not.toBeInTheDocument();
  });

  it('keeps the nested children when parsing the tasks response', () => {
    const parsed = taskListSchema.parse(treeResponse);

    expect(parsed[0].children?.[1].children?.[0].title).toBe('Book venue');
    expect(parsed[0].open_child_count).toBe(2);
    expect(flattenTaskTree(parsed).map((item) => item.title)).toEqual([
      'Work',
      'Write report',
      'Plan launch',
      'Book venue',
      'Standalone task',
      'JAN-9 Imported issue',
    ]);
  });

  it('offers Delete on local tasks and category headings, never on imported tasks (JAN-221)', async () => {
    const onDelete = vi.fn();
    render(<DashboardTaskTree tasks={treeResponse} onStatusChange={vi.fn()} onDelete={onDelete} />);

    await userEvent.click(screen.getByRole('button', { name: 'Delete Book venue' }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete Work' }));
    expect(onDelete.mock.calls.map(([deleted]) => deleted.title)).toEqual(['Book venue', 'Work']);
    expect(screen.queryByRole('button', { name: 'Delete JAN-9 Imported issue' })).not.toBeInTheDocument();
  });

  it('shows no Delete buttons without an onDelete handler', () => {
    render(<DashboardTaskTree tasks={treeResponse} onStatusChange={vi.fn()} />);

    expect(screen.queryByRole('button', { name: /^Delete / })).not.toBeInTheDocument();
  });
});

describe('DashboardTaskTree due dates (JAN-394)', () => {
  it('keeps an imported Google Tasks deadline on its calendar day west of UTC', () => {
    const previousTz = process.env.TZ;
    process.env.TZ = 'America/New_York';
    try {
      const imported = task({
        id: 50,
        title: 'File taxes',
        source: 'imported',
        source_label: 'Google Tasks',
        read_only: true,
        due_at: '2026-11-10T00:00:00',
      });
      render(<DashboardTaskTree tasks={[imported]} onStatusChange={vi.fn()} />);
      const expected = new Date(Date.UTC(2026, 10, 10)).toLocaleDateString(undefined, { timeZone: 'UTC' });
      const shiftedDay = new Date(Date.UTC(2026, 10, 10)).toLocaleDateString();
      expect(expected).not.toBe(shiftedDay); // New York sees midnight UTC as the previous day
      expect(screen.getByText(`Due ${expected}`)).toBeInTheDocument();
    } finally {
      if (previousTz === undefined) delete process.env.TZ;
      else process.env.TZ = previousTz;
    }
  });
});
