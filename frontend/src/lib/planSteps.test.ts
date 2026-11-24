import { describe, expect, it } from 'vitest';

import type { ToolCallRecord } from '../types';
import { buildTimeline, closeOpenSteps, parsePlanEvent, type PlanStepRecord, upsertPlanStep } from './planSteps';

const step = (overrides: Partial<PlanStepRecord> = {}): PlanStepRecord => ({
  step_id: 'step-1',
  index: 1,
  status: 'pending',
  tool_names: ['search_emails'],
  ...overrides,
});

const call = (overrides: Partial<ToolCallRecord> = {}): ToolCallRecord => ({
  call_id: 'run-1',
  tool_id: 'mail',
  tool_name: 'search_emails',
  phase: 'start',
  args: null,
  args_truncated: false,
  result_preview: null,
  result_truncated: false,
  duration_ms: null,
  step_id: 'step-1',
  ...overrides,
});

describe('parsePlanEvent', () => {
  it('reads a frame body and rejects malformed ones', () => {
    expect(parsePlanEvent('{"step_id":"step-2","index":2,"status":"running","tool_names":["a",3]}')).toEqual({
      step_id: 'step-2',
      index: 2,
      status: 'running',
      tool_names: ['a'],
    });
    expect(parsePlanEvent('{not json')).toBeNull();
    expect(parsePlanEvent('{"step_id":"s","status":"exploded"}')).toBeNull();
    expect(parsePlanEvent('{"status":"running"}')).toBeNull();
  });
});

describe('upsertPlanStep', () => {
  it('appends new steps and moves a step through its lifecycle', () => {
    let steps = upsertPlanStep(undefined, step());
    steps = upsertPlanStep(steps, step({ status: 'running' }));
    steps = upsertPlanStep(steps, step({ step_id: 'step-2', index: 2, tool_names: ['list_topics'] }));
    steps = upsertPlanStep(steps, step({ status: 'completed' }));
    expect(steps.map((s) => [s.step_id, s.status])).toEqual([
      ['step-1', 'completed'],
      ['step-2', 'pending'],
    ]);
  });

  it('never reopens a finished step', () => {
    const steps = upsertPlanStep([step({ status: 'failed' })], step({ status: 'running' }));
    expect(steps[0].status).toBe('failed');
  });
});

describe('closeOpenSteps', () => {
  it('fails unfinished steps and leaves finished ones alone', () => {
    const steps = [step({ status: 'completed' }), step({ step_id: 'step-2', index: 2, status: 'running' })];
    expect(closeOpenSteps(steps)?.map((s) => s.status)).toEqual(['completed', 'failed']);
    const finished = [step({ status: 'completed' })];
    expect(closeOpenSteps(finished)).toBe(finished);
    expect(closeOpenSteps(undefined)).toBeUndefined();
  });
});

describe('buildTimeline', () => {
  it('groups calls by step in step order', () => {
    const timeline = buildTimeline(
      [step({ step_id: 'step-2', index: 2, status: 'running' }), step({ status: 'completed' })],
      [call({ phase: 'end' }), call({ call_id: 'run-2', step_id: 'step-2' })]
    );
    expect(timeline.steps.map((entry) => [entry.step.step_id, entry.status, entry.calls.map((c) => c.call_id)])).toEqual([
      ['step-1', 'completed', ['run-1']],
      ['step-2', 'running', ['run-2']],
    ]);
    expect(timeline.ungrouped).toEqual([]);
  });

  it('shows a step as waiting while one of its calls awaits approval', () => {
    const request = {
      call_id: 'run-1',
      tool_id: 'mail.send',
      tool_name: 'send_email',
      display_name: 'Send mail',
      risk_level: 'high',
      reason: '',
      args: null,
      args_truncated: false,
      timeout_seconds: 300,
    };
    const waiting = buildTimeline([step({ status: 'running' })], [call({ approvalRequest: request })]);
    expect(waiting.steps[0].status).toBe('waiting');
    // A call created from the approval request alone (no step yet) joins the open step.
    const early = buildTimeline([step({ status: 'running' })], [call({ step_id: undefined, approvalRequest: request })]);
    expect(early.steps[0].status).toBe('waiting');
    // Once the step finished, it is no longer waiting.
    expect(buildTimeline([step({ status: 'failed' })], [call({ approvalRequest: request })]).steps[0].status).toBe('failed');
  });

  it('keeps calls naming an unknown step apart', () => {
    const timeline = buildTimeline([step()], [call({ step_id: 'step-9' })]);
    expect(timeline.ungrouped.map((c) => c.call_id)).toEqual(['run-1']);
  });
});
