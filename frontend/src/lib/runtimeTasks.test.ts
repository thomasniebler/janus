import { afterEach, describe, expect, it, vi } from 'vitest';

import api from '../api';
import { completedTaskDetail, taskPage, taskSummarySample } from '../components/features/tasks/taskFixtures';
import {
  isActiveTask,
  readTaskRef,
  runtimeTaskApi,
  stepProgress,
  taskDetailPath,
  taskRef,
} from './runtimeTasks';

describe('runtimeTasks helpers (JAN-204)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('round-trips a task reference through the page URL', () => {
    const path = taskDetailPath({ kind: 'trigger', ref_id: 'a b/c' });
    expect(path).toBe('/tasks?kind=trigger&task=a+b%2Fc');
    expect(readTaskRef(new URLSearchParams(path.split('?')[1]))).toEqual({ kind: 'trigger', ref_id: 'a b/c' });
  });

  it('ignores an incomplete or unknown selection', () => {
    expect(readTaskRef(new URLSearchParams('kind=chat&task=1'))).toBeNull();
    expect(readTaskRef(new URLSearchParams('kind=delegated'))).toBeNull();
    expect(readTaskRef(new URLSearchParams(''))).toBeNull();
  });

  it('links definition runs as trigger runs and delegated tasks as delegations', () => {
    expect(taskRef({ source: 'definition', ref_id: 'r' })).toEqual({ kind: 'trigger', ref_id: 'r' });
    expect(taskRef({ source: 'trigger', ref_id: 'r' })).toEqual({ kind: 'trigger', ref_id: 'r' });
    expect(taskRef({ source: 'delegated', ref_id: 'd' })).toEqual({ kind: 'delegated', ref_id: 'd' });
  });

  it('reports progress and whether a task is still in motion', () => {
    expect(stepProgress({ step_count: 0, completed_step_count: 0 })).toBeNull();
    expect(stepProgress({ step_count: 1, completed_step_count: 1 })).toBe('1 of 1 step done');
    expect(stepProgress({ step_count: 3, completed_step_count: 2 })).toBe('2 of 3 steps done');
    expect(isActiveTask({ status: 'queued' })).toBe(true);
    expect(isActiveTask({ status: 'running' })).toBe(true);
    expect(isActiveTask({ status: 'waiting' })).toBe(true);
    expect(isActiveTask({ status: 'exhausted' })).toBe(false);
  });

  it('calls the owner-scoped endpoints', async () => {
    const get = vi
      .spyOn(api, 'get')
      .mockResolvedValueOnce({ data: taskPage(taskSummarySample) })
      .mockResolvedValue({ data: completedTaskDetail });
    await runtimeTaskApi.list({ source: 'delegated', limit: 5 });
    await runtimeTaskApi.detail({ kind: 'trigger', ref_id: 't/1' });
    await runtimeTaskApi.detail({ kind: 'delegated', ref_id: 'd1' });
    expect(get.mock.calls.map((call) => call[0])).toEqual([
      '/api/runtime/tasks/',
      '/api/runtime/tasks/triggers/t%2F1',
      '/api/runtime/tasks/delegations/d1',
    ]);
    expect(get.mock.calls[0][1]).toEqual({ params: { source: 'delegated', limit: 5 } });
  });
});
