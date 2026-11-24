import type { PlannerTask } from './types';

export function flattenTasks(tasks: PlannerTask[]): PlannerTask[] {
  return tasks.flatMap((task) => [task, ...(task.children ? flattenTasks(task.children) : [])]);
}

export function getNextSelectedTaskId(current: number | null, tasks: PlannerTask[]): number | null {
  const visibleTasks = flattenTasks(tasks);
  if (current !== null && visibleTasks.some((task) => task.id === current)) {
    return current;
  }
  return visibleTasks[0]?.id ?? null;
}
