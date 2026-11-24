import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { TriggerWidget, type TriggerWidgetStyle } from './TriggerWidget';
import { TriggerWidgetList } from './TriggerWidgetList';
import { makeTrigger, triggerMatrix } from './triggerFixtures';
import {
  KIND_PRESENTATION,
  RUNTIME_TRIGGER_KINDS,
  RUNTIME_TRIGGER_STATUSES,
  STATUS_PRESENTATION,
  formatDuration,
  formatTimestamp,
  lifecycleSteps,
  triggerRunTitle,
} from './triggerPresentation';

const STYLES: TriggerWidgetStyle[] = ['row', 'card', 'timeline'];

describe('TriggerWidget', () => {
  describe.each(STYLES)('%s style', (widgetStyle) => {
    it.each(
      RUNTIME_TRIGGER_STATUSES.flatMap((status) =>
        RUNTIME_TRIGGER_KINDS.map((kind) => [status, kind] as const),
      ),
    )('renders a %s %s trigger with its identity and lifecycle facts', (status, kind) => {
      const trigger = makeTrigger(status, kind);
      render(<TriggerWidget trigger={trigger} widgetStyle={widgetStyle} />);

      const widget = screen.getByTestId('trigger-widget');
      expect(widget).toHaveAttribute('data-status', status);
      expect(widget).toHaveAttribute('data-kind', kind);
      expect(widget).toHaveAttribute('data-style', widgetStyle);
      expect(within(widget).getByText(trigger.task_id)).toBeInTheDocument();
      expect(within(widget).getByText(new RegExp(`${trigger.attempt_count} attempt`))).toBeInTheDocument();
      const statusLabel = STATUS_PRESENTATION[status].label;
      if (widgetStyle === 'row') {
        expect(within(widget).getByText(statusLabel)).toBeInTheDocument();
      } else {
        expect(within(widget).getByTestId('trigger-status-badge')).toHaveTextContent(statusLabel);
        expect(within(widget).getByTestId('trigger-kind-badge')).toHaveTextContent(
          KIND_PRESENTATION[kind].label,
        );
      }
      expect(widget).toHaveAccessibleName(
        `${KIND_PRESENTATION[kind].label} trigger ${triggerRunTitle(trigger)} (${trigger.task_id}) — ${STATUS_PRESENTATION[status].label}`,
      );
    });

    it('shows the failure detail only for failing statuses', () => {
      const failed = makeTrigger('failed', 'manual');
      const { unmount } = render(<TriggerWidget trigger={failed} widgetStyle={widgetStyle} />);
      expect(screen.getByTestId('trigger-last-error')).toHaveTextContent(failed.last_error ?? '');
      unmount();

      render(<TriggerWidget trigger={makeTrigger('completed', 'manual')} widgetStyle={widgetStyle} />);
      expect(screen.queryByTestId('trigger-last-error')).not.toBeInTheDocument();
    });

    it('falls back to the durable step error once a retry has cleared last_error', () => {
      // The worker clears `last_error` every time it re-claims a row, so an
      // exhausted trigger can arrive with no live error at all. Without the
      // plan-state fallback the human would be shown a bare status and no
      // reason whatsoever (JAN-192).
      const trigger = makeTrigger('exhausted', 'scheduled', {
        last_error: null,
        step_error: 'model provider unreachable',
      });
      render(<TriggerWidget trigger={trigger} widgetStyle={widgetStyle} />);

      expect(screen.getByTestId('trigger-last-error')).toHaveTextContent('model provider unreachable');
    });
  });

  it('shows what a completed run actually did, not just its status', () => {
    // The point of JAN-192's history surface: which tools ran, and what the
    // automation concluded.
    const trigger = makeTrigger('completed', 'scheduled');
    render(<TriggerWidget trigger={trigger} widgetStyle="card" />);

    const result = screen.getByTestId('trigger-result');
    expect(result).toHaveTextContent('Tools invoked: list_calendar_events');
    expect(result).toHaveTextContent('Two meetings today and nothing overdue');
    expect(screen.getByText('Result')).toBeInTheDocument();
  });

  it('explains a waiting run instead of showing it as failed or finished (JAN-207)', () => {
    const trigger = makeTrigger('waiting', 'manual');
    render(<TriggerWidget trigger={trigger} widgetStyle="card" />);

    expect(screen.getByTestId('trigger-status-badge')).toHaveTextContent('Waiting');
    expect(screen.getByTestId('trigger-waiting-note')).toHaveTextContent('resumes automatically');
    expect(screen.getByTestId('trigger-result')).toHaveTextContent('wait_for_delegated_tasks');
    expect(screen.queryByTestId('trigger-last-error')).not.toBeInTheDocument();
    expect(STATUS_PRESENTATION.waiting.isFailure).toBe(false);
  });

  it('shows no waiting note for any other status', () => {
    render(<TriggerWidget trigger={makeTrigger('queued', 'manual')} widgetStyle="card" />);

    expect(screen.queryByTestId('trigger-waiting-note')).not.toBeInTheDocument();
  });

  it('omits the result block entirely for a trigger that has not run yet', () => {
    render(<TriggerWidget trigger={makeTrigger('queued', 'manual')} widgetStyle="card" />);

    expect(screen.queryByTestId('trigger-result')).not.toBeInTheDocument();
    expect(screen.queryByText('Result')).not.toBeInTheDocument();
  });

  it('condenses the result to its summary line in the dense row style', () => {
    // The row is a ledger line, so it shows the automation's conclusion rather
    // than the tool-trace preamble the card renders in full.
    render(<TriggerWidget trigger={makeTrigger('completed', 'scheduled')} widgetStyle="row" />);

    const headline = screen.getByTestId('trigger-result-headline');
    expect(headline).toHaveTextContent('Two meetings today and nothing overdue');
    expect(headline).not.toHaveTextContent('Authorized tools');
  });

  it('surfaces the full lifecycle timestamps in the card style', () => {
    const trigger = makeTrigger('completed', 'scheduled');
    render(<TriggerWidget trigger={trigger} widgetStyle="card" />);

    const metaLabels = screen.getAllByRole('term').map((node) => node.textContent);
    expect(metaLabels).toEqual(['Created', 'Claimed', 'Completed', 'Attempts']);
    expect(screen.getByText(new Date(trigger.created_at).toLocaleString())).toBeInTheDocument();
    const duration = formatDuration(trigger.claimed_at, trigger.completed_at);
    expect(duration).not.toBeNull();
    expect(screen.getByText(`ran for ${duration}`)).toBeInTheDocument();
  });

  it('renders unreached lifecycle steps as pending in the timeline style', () => {
    const trigger = makeTrigger('queued', 'manual');
    render(<TriggerWidget trigger={trigger} widgetStyle="timeline" />);

    const steps = lifecycleSteps(trigger);
    expect(steps.filter((step) => !step.reached)).toHaveLength(2);
    expect(screen.getAllByText('pending')).toHaveLength(2);
    expect(screen.getByText(`source ${trigger.source} · id ${trigger.trigger_id}`)).toBeInTheDocument();
  });

  it('announces a scheduled retry when one is pending', () => {
    const trigger = makeTrigger('failed', 'event');
    render(<TriggerWidget trigger={trigger} widgetStyle="card" />);
    expect(
      screen.getByText(`retry after ${new Date(trigger.next_attempt_at ?? '').toLocaleString()}`),
    ).toBeInTheDocument();
  });

  it('tolerates missing and malformed timestamps without crashing', () => {
    const trigger = makeTrigger('running', 'manual', {
      claimed_at: 'not-a-date',
      completed_at: null,
      next_attempt_at: null,
    });
    render(<TriggerWidget trigger={trigger} widgetStyle="card" />);
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('never reports a negative duration for out-of-order timestamps', () => {
    expect(formatDuration('2026-09-21T09:00:00Z', '2026-09-21T08:00:00Z')).toBeNull();
    expect(formatDuration('2026-09-21T08:00:00Z', '2026-09-21T08:00:45Z')).toBe('45s');
    expect(formatDuration('2026-09-21T08:00:00Z', '2026-09-21T08:01:04Z')).toBe('1m 04s');
    expect(formatDuration('2026-09-21T08:00:00Z', '2026-09-21T10:05:00Z')).toBe('2h 05m');
  });

  it('reads offset-less server timestamps as UTC (JAN-394)', () => {
    expect(formatTimestamp('2026-09-21T08:00:00')).toBe(new Date('2026-09-21T08:00:00Z').toLocaleString());
    // A naive UTC end paired with an explicit-offset start must not be skewed by the local offset.
    expect(formatDuration('2026-09-21T08:00:00Z', '2026-09-21T08:00:45')).toBe('45s');
  });
});

describe('TriggerWidgetList', () => {
  it.each(STYLES)('renders one widget per trigger in the %s style', (widgetStyle) => {
    render(<TriggerWidgetList triggers={triggerMatrix} widgetStyle={widgetStyle} />);
    expect(screen.getAllByTestId('trigger-widget')).toHaveLength(triggerMatrix.length);
  });

  it('renders nothing but its frame when there are no triggers', () => {
    render(<TriggerWidgetList triggers={[]} />);
    expect(screen.queryByTestId('trigger-widget')).not.toBeInTheDocument();
  });
});
