import { describe, expect, it } from 'vitest';

import { getNextSelectedTaskId } from './taskTree';
import type { PlannerTask } from './types';

function task(id: number, title: string, children: PlannerTask[] = []): PlannerTask {
  return {
    id,
    title,
    description: null,
    status: 'Todo',
    urgency: 'normal',
    priority: null,
    due_at: null,
    scheduled_for: null,
    completed_at: null,
    parent_task_id: null,
    sort_order: null,
    child_count: children.length,
    open_child_count: children.length,
    read_only: false,
    source: 'manual',
    children,
  };
}

describe('getNextSelectedTaskId', () => {
  it('keeps a nested selection when the task is still present after refresh', () => {
    const tasks = [task(1, 'Parent', [task(2, 'Child')])];

    expect(getNextSelectedTaskId(2, tasks)).toBe(2);
  });

  it('falls back to the first visible task when the selected task disappears after refresh', () => {
    const tasks = [task(10, 'Still visible')];

    expect(getNextSelectedTaskId(99, tasks)).toBe(10);
  });

  it('clears the selection when no refreshed tasks remain visible', () => {
    expect(getNextSelectedTaskId(99, [])).toBeNull();
  });
});
